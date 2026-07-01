// Grey-box endpoint scanner.
//
// Reads a Spring Boot fat jar's own classes (BOOT-INF/classes) and emits, one
// JSON object per line, the HTTP endpoints each @RestController exposes:
//   {"http":"DELETE","path":"/admin-api/system/mail-account/delete-list",
//    "class":"cn.iocoder.yudao.module.system.controller.admin.mail.MailAccountController",
//    "method":"deleteMailAccountList","jar":"yudao-module-system-server"}
//
// It is deliberately dependency-free: it never imports spring-web. Mapping
// annotations are read *generically* by their runtime type name + reflectively
// invoking value()/path()/method(), so the same binary works for any Spring
// version present in the scanned jar. The controller->service->impl hop is NOT
// resolved here on purpose — that is left to on-demand decompilation + the LLM,
// which is the grey-box division of labour.
//
// Usage: java EndpointScanner <classesDir> <jarArtifactName> [libDir]
//   classesDir      : extracted BOOT-INF/classes directory
//   jarArtifactName : e.g. yudao-module-system-server (stamped into each row)
//   libDir          : optional BOOT-INF/lib directory; every *.jar in it is
//                     added to the loader classpath (a directory arg keeps the
//                     command line short — passing hundreds of jars overflows
//                     the Windows argv limit)

import java.io.File;
import java.lang.annotation.Annotation;
import java.lang.reflect.Method;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;
import java.util.stream.Stream;

public class EndpointScanner {

    // Simple annotation type name -> HTTP verb.
    private static String verbFor(String simpleName) {
        switch (simpleName) {
            case "GetMapping":    return "GET";
            case "PostMapping":   return "POST";
            case "PutMapping":    return "PUT";
            case "DeleteMapping": return "DELETE";
            case "PatchMapping":  return "PATCH";
            case "RequestMapping":return "ANY"; // refined below via method() attr
            default:              return null;
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("usage: EndpointScanner <classesDir> <jarName> [cp...]");
            System.exit(2);
        }
        Path classesDir = new File(args[0]).toPath();
        String jarName = args[1];

        List<URL> urls = new ArrayList<>();
        urls.add(classesDir.toUri().toURL());
        if (args.length >= 3) {
            File libDir = new File(args[2]);
            File[] jars = libDir.listFiles((d, n) -> n.endsWith(".jar"));
            if (jars != null) {
                for (File j : jars) urls.add(j.toURI().toURL());
            }
        }
        URLClassLoader loader = new URLClassLoader(
                urls.toArray(new URL[0]), EndpointScanner.class.getClassLoader());

        List<Path> classFiles;
        try (Stream<Path> s = Files.walk(classesDir)) {
            classFiles = s.filter(p -> p.toString().endsWith("Controller.class"))
                          .collect(Collectors.toList());
        }

        StringBuilder out = new StringBuilder();
        for (Path cf : classFiles) {
            String rel = classesDir.relativize(cf).toString()
                    .replace('\\', '/').replace('/', '.');
            String fqn = rel.substring(0, rel.length() - ".class".length());
            try {
                Class<?> c = Class.forName(fqn, false, loader);
                if (!isController(c)) continue;
                String base = classLevelPath(c);
                String prefix = apiPrefix(fqn);
                for (Method m : c.getDeclaredMethods()) {
                    emitMethod(out, c, m, base, prefix, jarName);
                }
            } catch (Throwable t) {
                System.err.println("skip " + fqn + ": " + t);
            }
        }
        System.out.print(out);
    }

    private static boolean isController(Class<?> c) {
        for (Annotation a : c.getAnnotations()) {
            String n = a.annotationType().getSimpleName();
            if (n.equals("RestController") || n.equals("Controller")) return true;
        }
        return false;
    }

    // yudao: admin controllers live in ...controller.admin..., app in ...controller.app...
    private static String apiPrefix(String fqn) {
        if (fqn.contains(".controller.admin")) return "/admin-api";
        if (fqn.contains(".controller.app"))   return "/app-api";
        return "";
    }

    private static String classLevelPath(Class<?> c) {
        for (Annotation a : c.getAnnotations()) {
            if (a.annotationType().getSimpleName().equals("RequestMapping")) {
                String[] v = readPaths(a);
                if (v.length > 0) return norm(v[0]);
            }
        }
        return "";
    }

    private static void emitMethod(StringBuilder out, Class<?> c, Method m,
                                   String base, String prefix, String jarName) {
        for (Annotation a : m.getAnnotations()) {
            String simple = a.annotationType().getSimpleName();
            String verb = verbFor(simple);
            if (verb == null) continue;
            if (verb.equals("ANY")) verb = requestMappingVerb(a);
            String[] paths = readPaths(a);
            if (paths.length == 0) paths = new String[]{""};
            for (String p : paths) {
                String full = prefix + base + norm(p);
                row(out, verb, full, c.getName(), m.getName(), jarName);
            }
        }
    }

    // Read value()/path() String[] off any mapping annotation, reflectively.
    private static String[] readPaths(Annotation a) {
        String[] r = invokeStringArray(a, "value");
        if (r.length == 0) r = invokeStringArray(a, "path");
        return r;
    }

    private static String requestMappingVerb(Annotation a) {
        try {
            Method mm = a.annotationType().getMethod("method");
            Object v = mm.invoke(a);
            if (v != null && v.getClass().isArray()) {
                int len = java.lang.reflect.Array.getLength(v);
                if (len > 0) return String.valueOf(java.lang.reflect.Array.get(v, 0));
            }
        } catch (Throwable ignore) { }
        return "ANY";
    }

    private static String[] invokeStringArray(Annotation a, String attr) {
        try {
            Method mm = a.annotationType().getMethod(attr);
            Object v = mm.invoke(a);
            if (v instanceof String[]) return (String[]) v;
            if (v instanceof String)   return new String[]{(String) v};
        } catch (Throwable ignore) { }
        return new String[0];
    }

    private static String norm(String p) {
        if (p == null || p.isEmpty()) return "";
        return p.startsWith("/") ? p : "/" + p;
    }

    private static void row(StringBuilder out, String http, String path,
                            String klass, String method, String jar) {
        out.append('{')
           .append("\"http\":\"").append(esc(http)).append("\",")
           .append("\"path\":\"").append(esc(path)).append("\",")
           .append("\"class\":\"").append(esc(klass)).append("\",")
           .append("\"method\":\"").append(esc(method)).append("\",")
           .append("\"jar\":\"").append(esc(jar)).append("\"}")
           .append('\n');
    }

    private static String esc(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}

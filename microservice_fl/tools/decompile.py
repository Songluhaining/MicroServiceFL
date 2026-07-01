"""fl_decompile_class — read a class's body from its deployed jar (grey-box)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

from microservice_fl.greybox.decompile import decompile_class


class DecompileInput(BaseModel):
    class_fqn: str = Field(
        description="Fully-qualified business class to decompile, e.g. "
        "cn.iocoder.yudao.module.system.service.mail.MailAccountServiceImpl"
    )


class DecompileClassTool(BaseTool):
    name = "fl_decompile_class"
    description = (
        "Decompile a single yudao business class from its deployed jar (CFR, no "
        "source needed) and return its Java source. Use to read a controller and "
        "follow its call into the *ServiceImpl, then read that method body to "
        "explain the root cause (slow SQL / Feign call / lock / null deref) and "
        "propose a fix. This is the grey-box replacement for reading the source."
    )
    input_model = DecompileInput

    def is_read_only(self, arguments: DecompileInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: DecompileInput, context: ToolExecutionContext) -> ToolResult:
        del context
        src, note = decompile_class(arguments.class_fqn)
        if src is None:
            return ToolResult(output=note, is_error=True)
        return ToolResult(output=f"// {note}\n{src}")

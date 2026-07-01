"""Grey-box artifacts: build an endpoint index and decompile classes from jars.

Runtime diagnosis stays source-free — these helpers turn the *deployed* jars
into the two things the agent needs: an endpoint->class index (``build_index``)
and on-demand class decompilation (``decompile``).
"""

import dis
import marshal
import sys

path = "/home/azer/CodeReviewQA/prototype/__pycache__/github_client.cpython-311.pyc"
with open(path, "rb") as f:
    f.read(16)
    code = marshal.load(f)

out = []
out.append("=== code object: name=%r argcount=%d varnames=%s" % (
    code.co_name, code.co_argcount, code.co_varnames))

def walk(co, indent=""):
    out.append(f"{indent}--- func {co.co_name}: args={co.co_varnames[:co.co_argcount]} "
               f"consts={co.co_consts} names={co.co_names}")
    for child in co.co_consts:
        if hasattr(child, "co_name"):
            walk(child, indent + "  ")

walk(code)
print("\n".join(out))
print("========= DISASSEMBLY (top-level) =========")
dis.dis(code)
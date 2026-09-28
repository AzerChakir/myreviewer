import io

cand = io.open("/mnt/c/Users/azerc/AppData/Local/Temp/opencode/ghc_candidate_1.txt", encoding="utf-8").read().splitlines()
edit = io.open("/mnt/c/Users/azerc/AppData/Local/Temp/opencode/upd_comment_new.txt", encoding="utf-8").read().rstrip("\n")
newpcb = io.open("/home/azer/CodeReviewQA/prototype/github_client.py", encoding="utf-8").read().rstrip("\n")

head = "\n".join(cand[:320])
out = head + "\n" + edit + "\n\n" + newpcb + "\n"
with io.open("/home/azer/CodeReviewQA/prototype/github_client.py", "w", encoding="utf-8") as f:
    f.write(out)
print("wrote lines:", out.count("\n") + 1, "bytes:", len(out.encode("utf-8")))
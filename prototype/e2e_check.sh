#!/bin/bash
UP=0
for i in $(seq 1 60); do
  if curl -sf -o /dev/null http://localhost:4200/ 2>/dev/null; then
    UP=1
    break
  fi
  sleep 1
done
if [ "$UP" -ne 1 ]; then
  echo "dashboard did not come up"
  exit 1
fi
echo "== root =="
curl -s http://localhost:4200/ | head -c 300
echo
echo "== /api/health via proxy =="
curl -s http://localhost:4200/api/health
echo
echo "== /api/reports =="
curl -s http://localhost:4200/api/reports
echo
echo "== report detail keys =="
curl -s http://localhost:4200/api/reports/demo-mixed-concerns | /home/azer/CodeReviewQA/.venv/bin/python -c "import json,sys; d=json.load(sys.stdin); print('keys:', sorted(d.keys())); print('change_log:', len(d.get('change_log') or []), 'entries'); print('post_review:', len(d.get('post_review') or []), 'findings'); print('verdict-nonempty:', bool(d.get('post_review_verdict')))"
echo "== detail body contains new section keywords =="
curl -s http://localhost:4200/api/reports/demo-mixed-concerns | grep -c "Review of the new code\|Before:\|After:" || true
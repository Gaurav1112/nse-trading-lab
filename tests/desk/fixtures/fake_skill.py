"""Stand-in for the vendored skill scripts. Behaviour is chosen by the first argument.

ok      print a JSON object with the args echoed
list    print a JSON list
sleep   sleep 5 seconds
fail    write a path-laden message to stderr and exit 1
garbage print non-JSON
empty1  print a JSON object and exit 1 (news.py does this when nothing is new)
env     print the sorted environment keys and the cwd
big     print a JSON object just over 2,000,000 bytes
"""
import json
import os
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
if mode == "ok":
    print(json.dumps({"args": sys.argv[2:]}))
elif mode == "list":
    print(json.dumps([{"a": 1}]))
elif mode == "sleep":
    time.sleep(5)
elif mode == "fail":
    print("starting", file=sys.stderr)
    print("facts: boom at /Users/someone/secret/dir/file.py", file=sys.stderr)
    sys.exit(1)
elif mode == "garbage":
    print("not json at all")
elif mode == "empty1":
    print(json.dumps({"items": []}))
    sys.exit(1)
elif mode == "env":
    print(json.dumps({"env": sorted(os.environ), "cwd": os.getcwd()}))
elif mode == "big":
    print(json.dumps({"pad": "x" * 2_000_000}))

set -e
grep -q argparse tool.py
[ "$(python3 tool.py)" = "hello world" ]
[ "$(python3 tool.py -v --name Bo --count 2)" = "$(printf '[verbose] hello Bo\n[verbose] hello Bo')" ]
set +e; python3 tool.py --bogus >/dev/null 2>&1; code=$?; set -e
[ "$code" -eq 2 ]
python3 -c "import tool; assert tool.greet('x', 2, False) == ['hello x', 'hello x']"

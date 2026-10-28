set -e
[ "$(python3 greet.py Ann)" = "Hello, Ann!" ]
[ "$(python3 greet.py --shout Ann)" = "HELLO, ANN!" ]

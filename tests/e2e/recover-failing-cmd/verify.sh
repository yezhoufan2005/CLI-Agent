set -e
sh run_checks.sh
printf 'id,value\n1,2,3\n' > bad.csv
! python3 -c "import validator; validator.check('bad.csv')" 2>/dev/null
grep -q 'validator.check' run_checks.sh

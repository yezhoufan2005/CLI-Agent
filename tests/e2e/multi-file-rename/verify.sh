set -e
python3 -m unittest -q
! grep -rn area_of_rect --include=*.py .
grep -q "def rectangle_area" shapes.py

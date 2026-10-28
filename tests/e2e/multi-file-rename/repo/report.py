from shapes import area_of_rect


def report(rects):
    return [area_of_rect(w, h) for w, h in rects]

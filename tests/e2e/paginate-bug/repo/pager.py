def page_count(total, per_page):
    """Number of pages needed to show `total` items, `per_page` per page."""
    return total // per_page


def get_page(items, page, per_page):
    """Return the items on 1-based page `page`."""
    start = page * per_page
    return items[start:start + per_page]

"""
test_parser2.py - Deep diagnostic for the 2 failing fields
Run: python test_parser2.py

Reads the already-saved profile_raw.html and portfolio_raw.html
(no extra network requests needed).
"""

from bs4 import BeautifulSoup

def load(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

def main():
    try:
        soup_p  = BeautifulSoup(load("profile_raw.html"),   "lxml")
        soup_pf = BeautifulSoup(load("portfolio_raw.html"),  "lxml")
    except Exception:
        return

    SEP = "=" * 65

    # ─────────────────────────────────────────────────────────────
    # BUG 1: title
    # ─────────────────────────────────────────────────────────────
    print(SEP)
    print("BUG 1: title — what does the HTML actually look like?")
    print(SEP)

    # Show the full <h1> that contains the name
    print("\n[h1 tags on page]")
    for h1 in soup_p.find_all("h1"):
        print(f"  classes={h1.get('class')}  text={h1.get_text(strip=True)!r:.80s}")
        print(f"  outerHTML: {str(h1)[:300]}\n")

    # Show every element with class containing 'usercard'
    print("\n[elements with 'usercard' in class]")
    for tag in soup_p.find_all(class_=lambda c: c and any("usercard" in x for x in c)):
        print(f"  <{tag.name} class={tag.get('class')}> text={tag.get_text(strip=True)!r:.80s}")

    # The scraper looks for ul.user__meta — show what's really there
    print("\n[ul.user__meta]")
    for ul in soup_p.select("ul.user__meta"):
        print(f"  found ul.user__meta: {str(ul)[:600]}")

    # Maybe it has a different class name — show all <ul> near the h1
    print("\n[all <ul> tags in the page — class names only]")
    for ul in soup_p.find_all("ul"):
        cls = ul.get("class")
        if cls:
            print(f"  ul classes: {cls}")

    # Show sibling/nearby elements around the name h1
    print("\n[context around the name h1 — parent and its children]")
    name_h1 = soup_p.select_one("h1 bdi")
    if name_h1:
        h1 = name_h1.find_parent("h1")
        parent = h1.find_parent() if h1 else None
        if parent:
            print(f"  parent tag: <{parent.name} class={parent.get('class')}>")
            print(f"  parent HTML (first 1000 chars):\n{str(parent)[:1000]}")

    # Look for fa-briefcase anywhere on the page
    print("\n[any element with class fa-briefcase]")
    for tag in soup_p.find_all(class_=lambda c: c and "fa-briefcase" in c):
        print(f"  found: <{tag.name} class={tag.get('class')}>")
        print(f"  parent: {str(tag.find_parent())[:300]}")

    # Look for the job title text "مهندس حاسوب" directly
    print("\n[searching for text 'مهندس' anywhere in page]")
    import re
    for tag in soup_p.find_all(string=re.compile("مهندس")):
        parent = tag.find_parent()
        print(f"  found text in <{parent.name} class={parent.get('class')}> id={parent.get('id')!r}")
        print(f"  text: {tag.strip()!r}")
        print(f"  grandparent: <{parent.find_parent().name} class={parent.find_parent().get('class')}>")
        print(f"  grandparent HTML: {str(parent.find_parent())[:400]}\n")

    # ─────────────────────────────────────────────────────────────
    # BUG 2: portfolio_count
    # ─────────────────────────────────────────────────────────────
    print(SEP)
    print("BUG 2: portfolio_count — profile page vs portfolio page")
    print(SEP)

    print("\n[profile page: is #portfolio-grid present?]")
    grid = soup_p.select_one("#portfolio-grid")
    print(f"  #portfolio-grid: {'FOUND' if grid else 'NOT FOUND'}")

    print("\n[profile page: is #portfolio tab div present?]")
    tab = soup_p.select_one("#portfolio")
    print(f"  #portfolio div: {'FOUND' if tab else 'NOT FOUND'}")
    if tab:
        print(f"  #portfolio content (first 400):\n  {str(tab)[:400]}")

    print("\n[portfolio page: #portfolio-grid found?]")
    grid_pf = soup_pf.select_one("#portfolio-grid")
    print(f"  #portfolio-grid: {'FOUND' if grid_pf else 'NOT FOUND'}")
    if grid_pf:
        items = grid_pf.select("div.postcard.cell-container")
        print(f"  items count: {len(items)}")
        print(f"  first item HTML:\n{str(items[0])[:500] if items else 'none'}")

    print("\n[portfolio page: is there a 'load more' / pagination?]")
    more = soup_pf.select_one(".load--more")
    print(f"  .load--more: {'FOUND' if more else 'NOT FOUND'}")
    if more:
        print(f"  HTML: {str(more)[:300]}")

    pager = soup_pf.select_one("[data-filter='pager']")
    print(f"  [data-filter=pager]: {'FOUND — more pages exist!' if pager else 'NOT FOUND'}")
    if pager:
        print(f"  HTML: {str(pager)[:300]}")

    print("\n" + SEP)
    print("DONE")
    print(SEP)

if __name__ == "__main__":
    main()

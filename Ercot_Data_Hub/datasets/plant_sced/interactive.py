#!/usr/bin/env python3
"""
Interactive ERCOT plant SCED puller — driven by simple prompts.
Launched by run.command (double-click). Also runnable: python interactive.py
"""
import sys
import pandas as pd
import sced_plants as sp


def ask(prompt, default=""):
    try:
        val = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        sys.exit(0)
    return val or default


def choose_resources(reg):
    """Narrow the registry, show a numbered list, return selected names."""
    while True:
        counts = reg.groupby("fuel_group")["resource_name"].count().sort_values(ascending=False)
        print("\nFuel groups:", "  ".join(f"{f}({n})" for f, n in counts.items()))
        q = ask("\nFilter by fuel group, or type part of a plant name (blank = all): ")

        sub = reg
        if q:
            groups = {g.lower(): g for g in reg["fuel_group"].unique()}
            if q.lower() in groups:
                sub = reg[reg["fuel_group"] == groups[q.lower()]]
            else:
                sub = reg[reg["resource_name"].str.contains(q, case=False, na=False)]

        sub = sub.sort_values("resource_name").reset_index(drop=True)
        if sub.empty:
            print("  Nothing matched — try again.")
            continue
        if len(sub) > 60:
            print(f"  {len(sub)} matches — too many to list. Narrow it further.")
            continue

        has_names = "plant_name" in sub.columns
        print(f"\n  {'#':>3}  {'RESOURCE':<22} {'FUEL':<13} {'PLANT NAME' if has_names else ''}")
        for i, r in sub.iterrows():
            pn = r["plant_name"] if has_names else ""
            print(f"  {i:>3}  {r['resource_name']:<22} {r['fuel_group']:<13} {pn}")

        pick = ask("\nSelect by number(s) e.g. 0,2,5 (or names, comma-separated; blank = re-filter): ")
        if not pick:
            continue
        chosen = []
        for tok in pick.split(","):
            tok = tok.strip()
            if not tok:
                continue
            if tok.isdigit() and int(tok) in sub.index:
                chosen.append(sub.loc[int(tok), "resource_name"])
            else:
                chosen.append(tok.upper())
        chosen = list(dict.fromkeys(chosen))
        if chosen:
            return chosen
        print("  No valid selection — try again.")


def choose_dates():
    latest = sp.latest_available_date()
    print(f"\nERCOT publishes with a ~60-day lag — data is available through ~{latest}.")
    yr = ask("Enter a YEAR for the whole year (e.g. 2025), or press Enter to give a date range: ")
    if yr:
        return f"{yr}-01-01", f"{yr}-12-31"
    start = ask("Start date (YYYY-MM-DD): ")
    end = ask(f"End date (YYYY-MM-DD) [default {latest}]: ", str(latest))
    return start, end


def main():
    print("=" * 60)
    print("  ERCOT Plant SCED Puller")
    print("=" * 60)
    reg = sp.load_registry()
    print(f"{len(reg)} resources available (source: ERCOT 60-Day SCED Disclosure).")

    resources = choose_resources(reg)
    print(f"\nSelected: {', '.join(resources)}")
    start, end = choose_dates()

    print(f"\nAbout to fetch {len(resources)} resource(s) from {start} to {end}.")
    if ask("Proceed? [Y/n]: ", "y").lower().startswith("n"):
        print("Cancelled.")
        return

    results = sp.fetch_plants(resources, start, end)
    total = sum(len(df) for df in results.values())

    if total and ask("\nAlso export a combined CSV? [y/N]: ", "n").lower().startswith("y"):
        import os
        frames = [df for df in results.values() if not df.empty]
        combined = pd.concat(frames, ignore_index=True)
        os.makedirs("csv_exports", exist_ok=True)
        name = resources[0] if len(resources) == 1 else f"{len(resources)}plants"
        path = os.path.join("csv_exports", f"sced_{name}_{start}_{end}.csv")
        combined.to_csv(path, index=False)
        print(f"CSV written: {path}")

    print(f"\nDone — {total:,} intervals stored under data/  (parquet per plant per year).")


if __name__ == "__main__":
    main()
    try:
        input("\nPress Enter to close.")
    except (EOFError, KeyboardInterrupt):
        pass

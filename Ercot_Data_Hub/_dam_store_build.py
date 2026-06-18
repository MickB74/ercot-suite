import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import warnings; warnings.filterwarnings("ignore")
from ercot_core.bootstrap import setup_path; setup_path()
from ercot_core import dam_prices as DAM
from ercot_core import tz
from ercot_core.settlement_points import HUBS
yr = tz.now_central().year
for y in (yr-1, yr):
    n = DAM.build_dam_store(HUBS, f"{y}-01-01", f"{y}-12-31", log=lambda m: None)
    print(f"DAM store {y}: {n:,} rows total", flush=True)
print("DAM store ->", DAM.DAM_STORE, flush=True)

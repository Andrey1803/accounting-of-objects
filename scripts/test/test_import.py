import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import app_objects
    print("Import OK")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

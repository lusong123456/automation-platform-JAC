
import sys, os
sys.path.insert(0, 'c:\\Program_sl\\work\\项目\\江淮智能合同审核\\自动化测试\\automation-regression-platform')
os.environ.setdefault('APP_ID','htglxt')
os.environ.setdefault('APP_SECRET','mqhs14ulaj46kg3dpu')
os.environ.setdefault('APP_EMPLOYEE_ID','JK20260034')
os.environ.setdefault('BASE_URL','http://192.168.191.144:11000/prod-api')
from dotenv import load_dotenv
load_dotenv('c:\\Program_sl\\work\\项目\\江淮智能合同审核\\自动化测试\\automation-regression-platform/deploy/.env')

errors = 0

# 1) collector
print("=" * 50)
print("[1] pytest accuracy 用例收集")
try:
    from server.core import collector
    manifest = collector.get_cases(force=True)
    acc_count = 0
    for m in manifest['modules']:
        mod_name = m.get('module', '?')
        for c in m['cases']:
            if 'ACC' in c['id']:
                print("  OK  id=%s  module=%s  name=%s" % (c['id'], mod_name, c['name']))
                acc_count += 1
    print("  ACC 用例总数: %d" % acc_count)
    print("  全部用例数: %d" % manifest['total'])
    if acc_count == 0:
        print("  ERROR: 没收集到任何 ACC 用例")
        errors += 1
except Exception as e:
    print("  FAILED:", e)
    import traceback; traceback.print_exc()
    errors += 1

# 2) db + accuracy
print()
print("=" * 50)
print("[2] db / accuracy API import")
try:
    from server import db
    db.init_db()
    print("  OK db.init_db 成功")
    print("  OK list_accuracy_runs:", hasattr(db, 'list_accuracy_runs'))
    print("  OK get_accuracy_detail:", hasattr(db, 'get_accuracy_detail'))
    print("  OK save_accuracy_detail:", hasattr(db, 'save_accuracy_detail'))
except Exception as e:
    print("  FAILED:", e); import traceback; traceback.print_exc(); errors += 1

try:
    from server.api import accuracy
    print("  OK accuracy.router:", accuracy.router)
except Exception as e:
    print("  FAILED:", e); import traceback; traceback.print_exc(); errors += 1

# 3) runner
print()
print("=" * 50)
print("[3] runner import + 源码检查")
try:
    from server.core import runner
    print("  OK runner 导入成功")
    import inspect
    src = inspect.getsource(runner._execute)
    print("  OK _execute 里有 ACCURACY_DIFF_FILE:", "ACCURACY_DIFF_FILE" in src)
    print("  OK _execute 里有 accuracy_diff.json:", "accuracy_diff.json" in src)
    print("  OK _execute 里有 save_accuracy_detail:", "save_accuracy_detail" in src)
except Exception as e:
    print("  FAILED:", e); import traceback; traceback.print_exc(); errors += 1

print()
print("=" * 50)
if errors == 0:
    print("全部通过")
else:
    print("有 %d 个错误" % errors)
    sys.exit(1)

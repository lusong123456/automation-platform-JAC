import sys, os, json
sys.path.insert(0, r'c:\Program_sl\work\项目\江淮智能合同审核\自动化测试\automation-regression-platform')
os.environ.setdefault('APP_ID','htglxt')
from dotenv import load_dotenv
load_dotenv(r'c:\Program_sl\work\项目\江淮智能合同审核\自动化测试\automation-regression-platform\deploy\.env')
from server import db

runs = db.list_accuracy_runs(10)
print('accuracy runs 数量:', len(runs))
for r in runs:
    print('  run_id=', r['run_id'], 'total_contracts=', r['total_contracts'])

if runs:
    rid = runs[0]['run_id']
    detail = db.get_accuracy_detail(rid)
    data = detail['accuracy_data']
    print()
    print('合同数:', len(data))
    print()
    for c in data:
        print('===', c['contractName'], '| diff=', c['diffCount'], '===')
        diffs = c['diffs']
        types = {}
        for d in diffs:
            if d['compliantDiff'] and not d['suggestionDiff']:
                t = 'compliant_only'
            elif d['suggestionDiff'] and not d['compliantDiff']:
                t = 'suggestion_only'
            elif d['compliantDiff'] and d['suggestionDiff']:
                t = 'both'
            else:
                t = 'other'
            types[t] = types.get(t, 0) + 1
        print('  差异类型分布:', types)
        for i, d in enumerate(diffs[:2]):
            print('  --- diff[', i, '] ---')
            print(json.dumps(d, ensure_ascii=False, indent=2)[:1000])
        print()

"""Self-contained test of v2.5 reference scope; no game files are distributed or required."""
from pathlib import Path
import json,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audit import load_scope,audit_scope,ScopeError

def test_catalog_and_read_only_report():
    s=load_scope(); assert len(s['entries'])==437
    assert sum(x['game']=='STORM1' for x in s['entries'])==238
    assert sum(x['game']=='STORM2' for x in s['entries'])==199
    with tempfile.TemporaryDirectory(prefix='naruto_scope_test_') as td:
        root=Path(td);a=root/'STORM1';b=root/'STORM2';r=root/'REPORT';a.mkdir();b.mkdir()
        one=a/'interface/telop/eng/movieTelopBin.xfbin'
        one.parent.mkdir(parents=True);one.write_bytes(b'NUCC'+(121).to_bytes(4,'big'))
        two=b/'event/WIN64/eng/messageInfo.bin.xfbin'
        two.parent.mkdir(parents=True);two.write_bytes(b'NUCC'+(121).to_bytes(4,'big'))
        result=audit_scope(a,b,r)
        assert result['files_in_reference']==437
        assert result['confirmed_original_files']=={'STORM1':1,'STORM2':1}
        assert one.read_bytes()==b'NUCC'+(121).to_bytes(4,'big')
        assert two.read_bytes()==b'NUCC'+(121).to_bytes(4,'big')
        report=json.loads((r/'NARUTO_PATCH_COVERAGE_REPORT.json').read_text(encoding='utf8'))
        assert len(report['records'])==437
        assert sum(x['existing_original_state']=='ignore_cache' for x in report['records'])==3
        try:audit_scope(a,b,a/'BAD')
        except ScopeError:pass
        else:raise AssertionError('Should refuse in-game output folder')

if __name__=='__main__':
    test_catalog_and_read_only_report()
    print('PASS: v2.5 reference scope, signature scan, report and source-tree guard')

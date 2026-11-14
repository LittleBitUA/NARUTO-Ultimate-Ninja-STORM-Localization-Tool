"""No proprietary samples required: STORM 3 read-only discovery regression."""
from __future__ import annotations
from pathlib import Path
import json
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit import AuditError, analyze_folder, run


def test_storm3_inventory_read_only_with_language_siblings(tmp_path):
    src=tmp_path/'Full Burst'/'data'/'disc'/'dataRegion_extracted'/'data'/'message'/'WIN32'/'eng'
    src.mkdir(parents=True)
    rus=src.parent/'rus'
    rus.mkdir()
    (rus/'translate.bin.xfbin').write_bytes(b'example')
    original=b'NUCC'+bytes(80)
    (src/'message.bin.xfbin').write_bytes(original)
    (src/'note.dat').write_bytes(b'Example')
    result=analyze_folder(src)
    assert result['files_total']==2
    assert result['xfbin_total']==1
    assert result['parsed_xfbin']==0
    assert result['language_siblings']['rus']['files']==1
    assert 'Full Burst' in result['edition_hint']
    files=run(src,tmp_path/'reports')
    assert Path(files['report_json']).is_file()
    assert Path(files['report_tsv']).read_bytes()[:3]==b'\xef\xbb\xbf'
    assert len(json.loads(Path(files['report_json']).read_text(encoding='utf-8'))['files'])==2
    assert (src/'message.bin.xfbin').read_bytes()==original
    # Running twice never overwrites old reports.
    run(src,tmp_path/'reports')
    assert len(list((tmp_path/'reports').glob('STORM3_AUDIT_*')))==2


def test_storm3_rejects_game_resource_output(tmp_path):
    src=tmp_path/'eng'; src.mkdir()
    (src/'file.bin').write_bytes(b'ok')
    with pytest.raises(AuditError):
        run(src,src/'reports')
    with pytest.raises(AuditError):
        analyze_folder(tmp_path)

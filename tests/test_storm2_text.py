"""STORM 2 regression tests. Requires the user's own sample via STORM2_TEST_FILE.

Run: set STORM2_TEST_FILE=E:\\...\\messageInfo.bin.xfbin
     py -3 -m pytest tests/test_storm2_text.py
No copyrighted game files are bundled in the Toolkit ZIP.
"""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import storm2_text as m

@pytest.fixture
def source(tmp_path):
    path=os.environ.get('STORM2_TEST_FILE')
    if not path or not Path(path).is_file():
        pytest.skip('Set STORM2_TEST_FILE to your own messageInfo.bin.xfbin')
    src=tmp_path/'english'/'messageInfo.bin.xfbin'
    src.parent.mkdir(parents=True)
    shutil.copy2(path,src)
    return src


def test_original_round_trip_and_exact_fields(source, tmp_path):
    doc=m.load(source)
    assert doc.count==31135
    assert len(doc.fields)==47859
    out=tmp_path/'ua'
    m.export_project(source,out)
    empty=m.compile_project(source,out,tmp_path/'build',dry_run=True)
    assert empty['modified_fields']==0
    unchanged,stat=m.build_bytes(doc,{})
    assert unchanged==source.read_bytes()
    assert stat['unchanged']


def test_modified_txt_full_pipeline_with_backups(source,tmp_path):
    original=m.load(source)
    out=tmp_path/'ua';build=tmp_path/'build'
    m.export_project(source,out)
    doc=m.read_manifest(out/m.MANIFEST_NAME)
    f=next(f for f in original.fields if f.source=='Autosave')
    initial=(out/m.TXT_NAME).read_text(encoding='utf-8-sig')
    wanted=m.make_header(f)+'\n\n###'
    assert initial.count(wanted)==1
    modified=initial.replace(wanted,m.make_header(f)+'\nАвтозбереження\n\n###',1)
    (out/m.TXT_NAME).write_bytes(modified.replace('\n','\r\n').encode('utf-8-sig'))
    dry=m.compile_project(source,out,build,dry_run=True)
    assert dry['modified_fields']==1
    result=m.compile_project(source,out,build,dry_run=False)
    assert result['modified_fields']==1
    compiled=build/m.BUILD_RELATIVE_PATH
    assert compiled.is_file()
    assert not (build/m.SOURCE_NAME).exists()
    import zipfile
    with zipfile.ZipFile(build/'STORM2_messageInfo_TEST_ONLY.zip') as z:
        assert z.namelist()==[m.BUILD_RELATIVE_PATH.as_posix()]
        assert z.read(m.BUILD_RELATIVE_PATH.as_posix())==compiled.read_bytes()
    final=m.load(compiled)
    assert final.fields[f.number-1].source=='Автозбереження'
    assert all(a.source==b.source for a,b in zip(original.fields,final.fields) if a.number!=f.number)
    # Re-export never discards old translations and produces backups.
    again=m.export_project(source,out)
    assert again['preserved_translations']==1
    assert len(list(out.glob('STORM2.ALL.UA.txt.backup_*')))==1
    assert len(list(out.glob('STORM2.ALL.manifest.json.backup_*')))==1
    assert source.read_bytes()==original.original


def test_protection_rules(source):
    doc=m.load(source)
    for f in doc.fields:
        if '<color ' in f.source:
            with pytest.raises(m.Storm2Error):
                m.validate_translation(f,f.source.replace('<color ','<wrong ',1))
            break
    for f in doc.fields:
        if '\n' in f.source:
            with pytest.raises(m.Storm2Error):
                m.validate_translation(f,'Змінений текст без переносу')
            break
    for f in doc.fields:
        if f.source.startswith('**No Translation ('):
            with pytest.raises(m.Storm2Error):
                m.validate_translation(f,'Змінений службовий маркер')
            break


def test_old_root_file_is_preserved_and_new_output_is_nested(source,tmp_path):
    out=tmp_path/'ua'; build=tmp_path/'build'
    m.export_project(source,out)
    document=m.load(source)
    f=next(f for f in document.fields if f.source=='Autosave')
    raw=(out/m.TXT_NAME).read_text(encoding='utf-8-sig')
    raw=raw.replace(m.make_header(f)+'\n\n###',m.make_header(f)+'\nАвтозбереження\n\n###',1)
    (out/m.TXT_NAME).write_bytes(raw.replace('\n','\r\n').encode('utf-8-sig'))
    build.mkdir()
    legacy=build/m.SOURCE_NAME
    legacy.write_bytes(b'OLD ROOT FILE MUST NOT BE TOUCHED')
    result=m.compile_project(source,out,build,dry_run=False)
    assert legacy.read_bytes()==b'OLD ROOT FILE MUST NOT BE TOUCHED'
    assert (build/m.BUILD_RELATIVE_PATH).is_file()
    assert result['legacy_root_file']==str(legacy)


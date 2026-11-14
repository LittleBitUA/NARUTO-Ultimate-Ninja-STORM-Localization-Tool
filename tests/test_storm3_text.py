from pathlib import Path
from storm3_text import UDoc,UField,layout,PROFILE,REL,LABELS


def make_doc(label,fields):
    return UDoc(label,Path(label+'.xfbin'),b'',label,0,0,0,2,4,fields)


def test_profiles_paths_no_overlap():
    assert set(PROFILE)==set(LABELS)==set(REL)
    assert len(set(map(str,REL.values())))==3
    assert 'WIN64' in str(REL['hd_win64'])
    assert 'WIN32' in str(REL['hd_win32'])
    assert PROFILE['hd_win64'][-1]==8
    assert PROFILE['original'][3]==16 and PROFILE['hd_win32'][3]==24


def test_dedup_by_identity_and_split_ambiguous():
    a=[UField(0,1,'AAAAAAAA','Same',20,'AAAAAAAA-F1',''),
       UField(1,2,'BBBBBBBB','Not identical',40,'BBBBBBBB-F2','')]
    b=[UField(1,1,'AAAAAAAA','Same ',20,'AAAAAAAA-F1',''),
       UField(0,2,'BBBBBBBB','Different meaning',40,'BBBBBBBB-F2','')]
    docs={'original':make_doc('original',a),'hd_win32':make_doc('hd_win32',b),
          'hd_win64':make_doc('hd_win64',b)}
    rows,bindings,conflicts=layout(docs)
    assert len(rows)==3 and len(conflicts)==1
    assert rows[0]['editions']==['original','hd_win32','hd_win64']
    assert [x['editions'] for x in rows[1:]]==[['original'],['hd_win32','hd_win64']]
    assert len(bindings['original'])==2 and len(bindings['hd_win32'])==2


def test_order_preserved_not_sorted_by_hash():
    a=[UField(0,1,'FFFFFFFF','First',20,'FFFFFFFF-F1',''),
       UField(1,1,'00000000','Second',40,'00000000-F1','')]
    b=[UField(0,1,'FFFFFFFF','First',20,'FFFFFFFF-F1',''),
       UField(1,1,'00000000','Second',40,'00000000-F1','')]
    d={'original':make_doc('original',a),'hd_win32':make_doc('hd_win32',b),'hd_win64':make_doc('hd_win64',b)}
    rows,_,_=layout(d)
    assert [r['source'] for r in rows]==['First','Second']

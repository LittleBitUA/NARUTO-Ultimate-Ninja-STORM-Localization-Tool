"""Regression test for safe incremental PNG export. Run from toolkit root with Python 3."""
import hashlib
import os
import pytest
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from textures import TextureError, export_textures, build_textures
from textures import batch_export_textures, batch_build_textures

# Optional arguments specify copies of original user-supplied XFBIN files.
DATA=Path(os.environ.get('STORM1_TEXTURE_TEST_DIR') or (sys.argv[1] if len(sys.argv)>1 and not sys.argv[1].startswith('-') else '/mnt/data'))

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def test():
    required=('name_1hkg_s.xfbin','cmn_nowloading.xfbin','pm01subt.xfbin','xmode.xfbin')
    if not all((DATA/n).is_file() for n in required):
        pytest.skip('STORM 1 texture regression requires the four user-provided XFBIN samples')
    with tempfile.TemporaryDirectory(prefix='naruto_v2_safe_png_') as td:
        work=Path(td)
        src=work/'data'/'disc'/'dataRegion_extracted'/'data'
        source=src/'interface'/'name'/'eng'/'name_1hkg_s.xfbin'
        source.parent.mkdir(parents=True)
        shutil.copy2(DATA/'name_1hkg_s.xfbin',source)
        workspace=work/'Textures_Workspace'
        result=batch_export_textures(src/'interface',workspace)
        assert result['exported_textures']==1,result
        png=workspace/'interface'/'name'/'eng'/'name_1hkg_s.xfbin'/'chunk_005.png'
        mf=png.parent/'textures_manifest.json'
        assert png.is_file() and mf.is_file()
        with Image.open(png) as image:
            im=image.convert('RGBA')
            im.putpixel((0,0),(232,22,56,255))
            im.save(png)
        edited=sha(png);manifest_hash=sha(mf)
        result=batch_export_textures(src/'interface',workspace)
        assert result['exported_textures']==0,result
        assert result['preserved_png']==1,result
        assert sha(png)==edited and sha(mf)==manifest_hash, 'repeated export changed user data'
        assert result['existing_untouched']==1,result
        # If a PNG was removed, restore only that file; keep other files.
        png.unlink()
        result=batch_export_textures(src/'interface',workspace)
        assert result['exported_textures']==1 and png.exists(),result
        assert sha(mf)==manifest_hash
        # Existing PNG with no manifest must be protected and re-associated safely.
        with Image.open(png) as image:
            im=image.convert('RGBA');im.putpixel((1,1),(0,10,200,255));im.save(png)
        edited=sha(png)
        mf.unlink()
        result=batch_export_textures(src/'interface',workspace)
        assert result['preserved_png']==1 and result['exported_textures']==0,result
        assert sha(png)==edited and mf.is_file()
        # Corrupt manifest: no writes to PNG or dangerous auto-repair.
        mf.write_text('{broken json',encoding='utf-8')
        result=batch_export_textures(src/'interface',workspace)
        assert result['protected_conflicts']==1 and sha(png)==edited,result
        # Restore a valid manifest and check source-hash mismatch protection.
        mf.unlink()
        result=batch_export_textures(src/'interface',workspace)
        assert result['protected_conflicts']==0,result
        shutil.copy2(DATA/'cmn_nowloading.xfbin',source)
        result=batch_export_textures(src/'interface',workspace)
        assert result['protected_conflicts']==1 and sha(png)==edited,result
        # Restore source and build PNG edit back to XFBIN: ensure roundtrip.
        shutil.copy2(DATA/'name_1hkg_s.xfbin',source)
        built=batch_build_textures(src/'interface',workspace,work/'Build')
        assert built['built_xfbin']==1,built
        assembled=work/'Build'/'data_win32'/'interface'/'name'/'eng'/'name_1hkg_s.xfbin'
        assert assembled.exists()
        # Full duplicate image path updates preserve existing SHA across both source trees.
        title=src/'adv_title'/'main'/'eng'/'pm01subt.xfbin'
        title.parent.mkdir(parents=True)
        shutil.copy2(DATA/'pm01subt.xfbin',title)
        out=batch_export_textures(src/'adv_title',workspace)
        assert out['exported_textures']==1,out
        out=batch_export_textures(src/'adv_title',workspace)
        assert out['preserved_png']==1 and out['exported_textures']==0,out
        # Legacy-manifest migration: old exporter saw only BC3 chunk, new sees BC1 too.
        xmode=src/'interface'/'mode'/'eng'/'xmode.xfbin'
        xmode.parent.mkdir(parents=True)
        shutil.copy2(DATA/'xmode.xfbin',xmode)
        folder=workspace/'interface'/'mode'/'eng'/'xmode.xfbin'
        out=export_textures(xmode,folder)
        assert out['textures']>=2 and out['new_png']>=2,out
        fmt0=folder/'chunk_209.png'
        fmt2=folder/'chunk_466.png'
        assert fmt0.exists() and fmt2.exists()
        with Image.open(fmt2) as image:
            im=image.convert('RGBA');im.putpixel((0,0),(22,180,95,255));im.save(fmt2)
        edited=sha(fmt2)
        info=json.loads((folder/'textures_manifest.json').read_text(encoding='utf-8'))
        info['entries']=[it for it in info['entries'] if it['chunk']==466]
        info['format']='STORM1_TEXTURES_NTP3_BC3_V1'
        (folder/'textures_manifest.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
        fmt0.unlink()
        out=export_textures(xmode,folder)
        assert out['new_png']==1 and out['preserved_png']==1 and out['new_manifest_entries']==1,out
        assert sha(fmt2)==edited
        doc=json.loads((folder/'textures_manifest.json').read_text(encoding='utf-8'))
        assert [e['chunk'] for e in doc['entries']] == [209,466]
        print('PASS: repeat, incomplete, missing manifest, malformed manifest, hash protection, Build, adv_title, BC1+BC3 legacy extension')

if __name__=='__main__': test()

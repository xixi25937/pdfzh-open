"""Offline layout inventory for source-free PDF reconstruction."""
import argparse
import hashlib
import json
from pathlib import Path
import pymupdf


def inspect(pdf, destination):
    from pdfzh_runtime import configure_layout_cache
    configure_layout_cache()
    from babeldoc.docvision.base_doclayout import DocLayoutModel
    from babeldoc.format.pdf.document_il.utils.raster_geometry import with_target_long_edge
    print('[本地] 正在加载版面识别模型…',flush=True)
    model=DocLayoutModel.load_onnx()
    result={'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'pages':[]}
    with pymupdf.open(pdf) as doc:
        for i,page in enumerate(doc):
            geometry=with_target_long_edge(page,72,1024,normalize_rotation=True)
            layout=model.predict(geometry.image[:,:,::-1],geometry=geometry)[0]
            regions=[{'kind':layout.names[int(b.cls)],'confidence':float(b.conf),
                      'bbox':[float(v) for v in b.xyxy]} for b in layout.boxes]
            result['pages'].append({'width':page.rect.width,'height':page.rect.height,'regions':regions})
            print(f'[本地] 第 {i+1}/{len(doc)} 页：'+', '.join(r['kind'] for r in regions),flush=True)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('pdf',type=Path);p.add_argument('destination',type=Path)
    a=p.parse_args();inspect(a.pdf,a.destination)

"""User-local settings. No credentials or personal paths belong in a checkout."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import base64
import hashlib
import json
import os
from pathlib import Path


def settings_path():
    base=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local'/'share')
    return base/'PDFZH'/'settings.json'


def load_settings():
    path=settings_path()
    if not path.exists():return {}
    try:
        value=json.loads(path.read_text(encoding='utf8'))
        if not isinstance(value,dict):raise ValueError()
        return value
    except (ValueError,OSError) as exc:
        raise RuntimeError('用户配置无法读取，请运行 pdfzh.cmd configure 重新配置。') from exc


def save_settings(value):
    path=settings_path();path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    os.replace(temp,path)


def protect_secret(value,*,decrypt=False):
    if os.name!='nt':raise RuntimeError('本版本的密钥保存仅支持 Windows；其他系统请设置 DEEPSEEK_API_KEY。')
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    raw=base64.b64decode(value) if decrypt else value.encode('utf8')
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    method=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    method.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    method.restype=wintypes.BOOL
    if not method(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise RuntimeError('无法读写 Windows 加密密钥，请在当前 Windows 账户重新配置。')
    try:result=ctypes.string_at(target.data,target.size)
    finally:kernel.LocalFree(ctypes.cast(target.data,ctypes.c_void_p))
    return result.decode('utf8') if decrypt else base64.b64encode(result).decode('ascii')


def api_key():
    if os.environ.get('DEEPSEEK_API_KEY','').strip():return os.environ['DEEPSEEK_API_KEY'].strip()
    value=load_settings().get('deepseek_dpapi')
    return protect_secret(value,decrypt=True) if value else ''


def set_key(key):
    if not key.strip():raise ValueError('API Key 不能为空')
    settings=load_settings();settings['deepseek_dpapi']=protect_secret(key.strip());save_settings(settings)


def job_directory(pdf,output=None):
    """Stable cache for reruns; content hash avoids same-name document collisions."""
    sha=hashlib.sha256()
    with pdf.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):sha.update(chunk)
    root=Path(output).expanduser().resolve() if output else pdf.parent/'翻译结果'
    return root/(pdf.stem[:80]+'-'+sha.hexdigest()[:10])

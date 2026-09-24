"""Atomic browser snapshot script and fingerprint helpers."""

from __future__ import annotations

from hashlib import sha256
from json import dumps
from typing import Any


READ_STATE = r"""() => {
  if (!document.body) return null;
  const cache = window.__webScraper ||= {nodes:new Map(), next:1};
  for (const [id,e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);
  const identity = e => {
    for (const [id,node] of cache.nodes) if (node === e) return id;
    const id = cache.next++; cache.nodes.set(id,e); return id;
  };
  const visible = e => !e.closest('[aria-hidden="true"],[inert]') && e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const label = e => (e.getAttribute('aria-label') || e.innerText || e.value || e.getAttribute('placeholder') || e.tagName).trim().slice(0,200);
  const role = e => e.getAttribute('role') || e.tagName.toLowerCase();
  const guard = e => {
    const r=e.getBoundingClientRect();
    return {label:label(e), role:role(e), value:e.value ?? null, checked:e.checked ?? null,
      readOnly:!!e.readOnly, disabled:!!e.disabled, expanded:e.getAttribute('aria-expanded'),
      rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
  };
  const selector = 'a[href],button,input,textarea,select,[role=button],[role=link],[role=combobox],[role=option]';
  const actions = [...document.querySelectorAll(selector)].map((e,i) => {
    const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2, node=identity(e);
    const options = e.tagName === 'SELECT' ? [...e.options].map(o => ({value:o.value,label:o.label})).filter(o=>!o.disabled) : [];
    return {id:'e'+node, node, selectorIndex:i, kind:e.tagName==='SELECT'?'select':(e.matches('input,textarea,[contenteditable=true]')?'fill':'click'), label:label(e), role:role(e), visible:!!(r.width&&r.height&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight), disabled:!!e.disabled, readOnly:!!e.readOnly, options, guard:guard(e)};
  }).filter(a=>a.visible&&!a.disabled);
  const text=document.body.innerText.slice(0,12000);
  const semantics=actions.map(({selectorIndex,visible,disabled,readOnly,rect,...a})=>a);
  const marker=[performance.timeOrigin,location.href,scrollX,scrollY,innerWidth,innerHeight,document.title,text,semantics];
  return {url:location.href,title:document.title,text,actions,scroll:{y:scrollY,height:document.documentElement.scrollHeight},semantics,marker};
}"""


def snapshot_fingerprint(state: dict[str, Any]) -> str:
    semantic = {
        "url": state.get("url"),
        "title": state.get("title"),
        "text": state.get("text"),
        "semantics": state.get("semantics", []),
    }
    return sha256(dumps(semantic, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


__all__ = ["READ_STATE", "snapshot_fingerprint"]

"""Interactive PDF.js preview with navigation and zoom inside fullscreen mode."""
import base64
import json


def full_page_viewer(pdf_bytes: bytes, initial_page: int = 1) -> str:
    """Return isolated HTML for Streamlit's components.html PDF viewer.

    The PDF stays in the user's browser iframe; no third-party PDF upload.
    The pinned PDF.js library is loaded from a CDN, but document bytes are not
    transmitted to that CDN by this code.
    """
    document_base64 = base64.b64encode(pdf_bytes).decode("ascii")
    html = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  *{box-sizing:border-box}
  html,body{margin:0;width:100%;height:100%;background:#f1f3f6;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#17212e}
  #viewer{height:100%;width:100%;display:flex;flex-direction:column;background:#f4f6f9;border:1px solid #d6dbe3;border-radius:12px;overflow:hidden}
  #viewer:fullscreen{height:100dvh;width:100vw;border:0;border-radius:0;background:#edf0f3}
  #toolbar{display:flex;align-items:center;gap:7px;flex-wrap:wrap;padding:9px 11px;background:#fff;border-bottom:1px solid #d8dee7;position:relative;z-index:2;flex-shrink:0}
  button{border:1px solid #c9d1dd;background:white;border-radius:7px;padding:9px 10px;color:#17212e;font-size:13px;cursor:pointer;touch-action:manipulation;min-height:38px;white-space:nowrap}
  button:disabled{opacity:.36;cursor:not-allowed}
  button:focus-visible,input:focus-visible{outline:2px solid #2476cc;outline-offset:1px}
  button[aria-pressed="true"],button.active{background:#dbeafd;border-color:#7db2f1}
  #pageInput{width:60px;min-height:38px;border:1px solid #c9d1dd;border-radius:7px;text-align:center;font-size:14px}
  #totalPages{color:#536175;min-width:44px;white-space:nowrap;font-size:13px}
  #range{min-width:70px;max-width:200px;flex:1;accent-color:#286fc2;cursor:pointer}
  #zoomValue{min-width:42px;color:#536175;font-size:12px;text-align:center}
  #spacer{flex:1}
  #viewport{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;display:flex;align-items:flex-start;justify-content:safe center;padding:14px;position:relative;touch-action:pan-x pan-y}
  #sheet{flex:none;line-height:0;max-width:none;box-shadow:0 3px 17px #1f293733;background:white}
  canvas{display:block}
  #status{font-size:12px;padding:5px 11px;background:#fff;border-top:1px solid #d8dee7;color:#64748b;min-height:27px;flex-shrink:0}
  #status.error{color:#a11212}
  #viewer:fullscreen #toolbar{padding:9px 12px}
  #viewer:fullscreen #range{max-width:320px}
  #viewport canvas{touch-action:pan-x pan-y}
  @media(max-width:650px){
    #viewer{height:100%}
    #toolbar{gap:5px;padding:7px}
    button{padding:7px 9px;min-height:36px}
    #range{order:5;max-width:100%;flex-basis:100%}
    #spacer{display:none}
    #viewport{padding:8px}
  }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
</head>
<body>
<div id="viewer" tabindex="0" aria-label="Interactive PDF page viewer">
 <div id="toolbar">
  <button id="prev" title="Previous page" aria-label="Previous page">◀</button>
  <input id="pageInput" type="number" min="1" value="1" aria-label="Go to PDF page">
  <span id="totalPages">/ …</span>
  <button id="next" title="Next page" aria-label="Next page">▶</button>
  <input id="range" type="range" min="1" max="1" value="1" aria-label="Slide PDF pages">
  <button id="minus" title="Zoom out" aria-label="Zoom out">−</button>
  <span id="zoomValue">100%</span>
  <button id="plus" title="Zoom in" aria-label="Zoom in">+</button>
  <button id="fitWidth" title="Fit page width">Fit width</button>
  <button id="fitPage" title="Fit whole page">Fit page</button>
  <span id="spacer"></span>
  <button id="full" title="Fullscreen with page and zoom controls">⛶ Fullscreen</button>
 </div>
 <div id="viewport" tabindex="0"><div id="sheet"><canvas id="canvas"></canvas></div></div>
 <div id="status" role="status" aria-live="polite">Loading PDF viewer…</div>
</div>
<script>
(function(){
  "use strict";
  const encoded = __PDF_BASE64__;
  const startPage = __INITIAL_PAGE__;
  const $ = (id) => document.getElementById(id);
  const viewer = $("viewer"), viewport = $("viewport"), canvas = $("canvas");
  const status = $("status"), range = $("range"), pageInput = $("pageInput");
  let pdf = null, pageNo = startPage, mode = "page", zoom = 1.0;
  let renderToken = 0, currentRender = null, queuedSlider = null, touchX=0, touchY=0;
  function say(message, error){
    status.textContent = message;
    status.className = error ? "error" : "";
  }
  function setControls(){
    if (!pdf) return;
    $("prev").disabled = pageNo<=1;
    $("next").disabled = pageNo>=pdf.numPages;
    range.max = pdf.numPages;
    range.value = pageNo;
    pageInput.max = pdf.numPages;
    pageInput.value = pageNo;
    $("totalPages").textContent = "/ " + pdf.numPages;
    $("fitWidth").classList.toggle("active", mode==="width");
    $("fitPage").classList.toggle("active", mode==="page");
    $("fitWidth").setAttribute("aria-pressed", mode==="width");
    $("fitPage").setAttribute("aria-pressed", mode==="page");
    $("zoomValue").textContent = Math.round(zoom*100)+"%";
  }
  async function showPage(){
    if (!pdf) return;
    const myToken = ++renderToken;
    if (currentRender) {
      try {currentRender.cancel();} catch(e){}
      currentRender = null;
    }
    setControls();
    say("Rendering page "+pageNo+" / "+pdf.numPages+"…", false);
    try {
      const page = await pdf.getPage(pageNo);
      if (myToken!==renderToken) return;
      const base = page.getViewport({scale:1});
      const maxWidth = Math.max(100, viewport.clientWidth-28);
      const maxHeight = Math.max(100, viewport.clientHeight-28);
      const widthFit = maxWidth/base.width;
      const fit = mode==="page" ? Math.min(widthFit,maxHeight/base.height) : widthFit;
      const scale = Math.max(0.12, Math.min(5, fit*zoom));
      const dims = page.getViewport({scale:scale});
      const pixelRatio = Math.min(window.devicePixelRatio||1,2);
      const safeRatio = Math.min(pixelRatio, Math.sqrt(16000000/(dims.width*dims.height)));
      canvas.width = Math.round(dims.width*safeRatio);
      canvas.height = Math.round(dims.height*safeRatio);
      canvas.style.width = dims.width+"px";
      canvas.style.height = dims.height+"px";
      const ctx = canvas.getContext("2d", {alpha:false});
      currentRender = page.render({
        canvasContext:ctx,
        viewport:dims,
        transform:[safeRatio,0,0,safeRatio,0,0],
        background:"white"
      });
      await currentRender.promise;
      if(myToken===renderToken) {
        say("Page "+pageNo+" of "+pdf.numPages+
          " · Use ◀ ▶, slider, zoom, keyboard arrows or swipe to navigate",false);
      }
    } catch(e){
      if(e && e.name==="RenderingCancelledException") return;
      if(myToken===renderToken) say("Unable to render PDF page: "+String(e.message||e),true);
    } finally {
      if(myToken===renderToken) currentRender = null;
    }
  }
  function jump(value){
    if(!pdf) return;
    const n=Number(value);
    if(!Number.isFinite(n)) return;
    const bounded=Math.min(pdf.numPages,Math.max(1,Math.round(n)));
    if(bounded!==pageNo){pageNo=bounded; viewport.scrollTop=0; viewport.scrollLeft=0; showPage();}
    else setControls();
  }
  function setZoom(next){
    zoom=Math.max(.4,Math.min(3,Math.round(next*100)/100));
    showPage();
  }
  $("prev").onclick=()=>jump(pageNo-1);
  $("next").onclick=()=>jump(pageNo+1);
  pageInput.onchange=()=>jump(pageInput.value);
  pageInput.onkeydown=(event)=>{if(event.key==="Enter"){jump(pageInput.value);pageInput.blur();}};
  range.oninput=()=>{
    clearTimeout(queuedSlider);
    const requested=range.value;
    pageInput.value=requested;
    queuedSlider=setTimeout(()=>jump(requested),90);
  };
  $("minus").onclick=()=>setZoom(zoom-.2);
  $("plus").onclick=()=>setZoom(zoom+.2);
  $("fitWidth").onclick=()=>{mode="width";zoom=1;showPage();};
  $("fitPage").onclick=()=>{mode="page";zoom=1;showPage();};
  $("full").onclick=async()=>{
    try {
      if(document.fullscreenElement) await document.exitFullscreen();
      else if(viewer.requestFullscreen) await viewer.requestFullscreen();
      else throw new Error("Fullscreen is not supported by this browser.");
    } catch(e) {
      say("Browser blocked fullscreen. All page and zoom controls still work here.",true);
    }
  };
  document.addEventListener("fullscreenchange",()=>{
    $("full").textContent=document.fullscreenElement?"⛶ Exit fullscreen":"⛶ Fullscreen";
    setTimeout(showPage,140);
  });
  document.addEventListener("keydown",(event)=>{
    const tag=(event.target && event.target.tagName)||"";
    if(tag==="INPUT" || tag==="TEXTAREA") return;
    if(event.key==="ArrowRight" || event.key==="PageDown"){event.preventDefault();jump(pageNo+1);}
    if(event.key==="ArrowLeft" || event.key==="PageUp"){event.preventDefault();jump(pageNo-1);}
    if(event.key==="Home"){event.preventDefault();jump(1);}
    if(event.key==="End" && pdf){event.preventDefault();jump(pdf.numPages);}
    if(event.key==="Escape" && document.fullscreenElement){document.exitFullscreen();}
  });
  viewport.addEventListener("touchstart",(event)=>{
    if(event.touches.length!==1)return;
    touchX=event.touches[0].clientX;touchY=event.touches[0].clientY;
  },{passive:true});
  viewport.addEventListener("touchend",(event)=>{
    if(event.changedTouches.length!==1)return;
    const dx=event.changedTouches[0].clientX-touchX;
    const dy=event.changedTouches[0].clientY-touchY;
    if(Math.abs(dx)>90 && Math.abs(dx)>Math.abs(dy)*1.5){
      jump(pageNo+(dx<0?1:-1));
    }
  },{passive:true});
  let resizeTimeout;
  window.addEventListener("resize",()=>{
    clearTimeout(resizeTimeout);
    resizeTimeout=setTimeout(showPage,160);
  });
  async function init(){
    if(typeof pdfjsLib==="undefined"){
      say("PDF viewer library could not load. Check your internet connection and try again.",true);
      return;
    }
    try{
      pdfjsLib.GlobalWorkerOptions.workerSrc=
        "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
      const binary=atob(encoded);
      const data=new Uint8Array(binary.length);
      for(let i=0;i<binary.length;i++) data[i]=binary.charCodeAt(i);
      pdf=await pdfjsLib.getDocument({data:data,isEvalSupported:false}).promise;
      pageNo=Math.max(1,Math.min(pdf.numPages,startPage));
      showPage();
    }catch(e){say("Unable to open PDF: "+String(e.message||e),true);}
  }
  init();
})();
</script>
</body>
</html>'''
    return (
        html.replace("__PDF_BASE64__", json.dumps(document_base64))
        .replace("__INITIAL_PAGE__", str(max(1, int(initial_page))))
    )

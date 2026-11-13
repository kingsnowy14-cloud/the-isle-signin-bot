(() => {
  const page = location.pathname.replace(/\/$/, '') || '/';
  const elementPath = el => {
    const path = [];
    let node = el;
    while (node && node !== document.body) {
      const parent = node.parentElement;
      if (!parent) break;
      const siblings = [...parent.children].filter(item => item.tagName === node.tagName);
      path.unshift(node.tagName.toLowerCase() + ':' + siblings.indexOf(node));
      node = parent;
    }
    return path.join('/');
  };
  const key = el => '__visual_shared__|' + elementPath(el);
  const legacyKey = el => page + '|' + elementPath(el);
  const safeHTML = value => {
    const doc = new DOMParser().parseFromString(String(value), 'text/html');
    const allowed = new Set(['B','STRONG','I','EM','U','S','SPAN','BR','FONT','IMG']);
    doc.body.querySelectorAll('*').forEach(el => {
      if (!allowed.has(el.tagName)) { el.replaceWith(...el.childNodes); return; }
      [...el.attributes].forEach(attr => {
        if (el.tagName === 'IMG' && attr.name === 'src' && attr.value.startsWith('/uploads/')) return;
        if (el.tagName === 'IMG' && attr.name === 'alt') return;
        if (el.tagName === 'IMG' && attr.name === 'class' && /^(visual-editor-added-image|visual-editor-logo)( visual-editor-added-image| visual-editor-logo)*$/.test(attr.value)) return;
        if (el.tagName === 'IMG' && ['width','height'].includes(attr.name) && /^\d{1,4}$/.test(attr.value)) return;
        if (attr.name === 'style') {
          const safe = [];
          for (const part of attr.value.split(';')) {
            const ix = part.indexOf(':');
            if (ix < 0) continue;
            const prop = part.slice(0, ix).trim().toLowerCase();
            const val = part.slice(ix + 1).trim();
            if (['color','font-size','font-family','font-weight','font-style','text-decoration'].includes(prop) && !/url\s*\(|expression|javascript/i.test(val)) safe.push(prop + ':' + val);
          }
          el.setAttribute('style', safe.join(';'));
        } else if (!['color','face','size'].includes(attr.name)) el.removeAttribute(attr.name);
      });
    });
    return doc.body.innerHTML;
  };
  function globalBackgroundCss(value){
    if(!value)return '';
    let gradient=value;
    if(/^#[0-9a-f]{6}$/i.test(value)){
      const rgb=value.match(/[0-9a-f]{2}/ig).map(v=>parseInt(v,16));
      const blend=(target,amount)=>rgb.map((v,i)=>Math.round(v*(1-amount)+target[i]*amount));
      const mid=blend([20,12,15],.58).map(v=>v.toString(16).padStart(2,'0')).join('');
      gradient='linear-gradient(125deg, '+value+' 0%, #'+mid+' 52%, #0c0b0d 100%)';
    }
    return 'html,body{background:'+gradient+'!important;background-attachment:fixed!important;}'+
      'body{transition:background 700ms ease!important;}'+
      'body>main,body>header,body>footer,main>section,main>header,main>footer,.hero,.section,.cta,.store-bottom{background-color:transparent!important;background-image:none!important;transition:background 700ms ease!important;}';
  }
  fetch('/api/visual-edits-public', { cache: 'no-store' })
    .then(response => { if (!response.ok) throw new Error('Live editor data unavailable'); return response.json(); })
    .catch(() => fetch('/static/visual-edits.json?v=20261113-live-public-api', { cache: 'no-store' }).then(response => response.ok ? response.json() : null))
    .then(data => {
      if (!data || typeof data !== 'object') return;
      const changes = data.content || {};
      const prices = data.tierPrices || {};
      const layout = data.layout || {};
      const images = data.images || {};
      const styles = data.styles || {};
      if (styles['__visual_global__']) { const g=styles['__visual_global__']; if(g.color)document.body.style.color=g.color; let tag=document.getElementById('ve-global-style'); if(!tag){tag=document.createElement('style');tag.id='ve-global-style';document.head.appendChild(tag);} tag.textContent=(g.color?'body *{color:'+g.color+'!important;}':'')+globalBackgroundCss(g.background); }
      if (styles[key(document.body)] || styles[legacyKey(document.body)]) Object.assign(document.body.style, styles[key(document.body)] || styles[legacyKey(document.body)]);
      document.querySelectorAll('body *').forEach(el => {
        const id = key(el), old = legacyKey(el);
        const image = images[id] || images[old], position = layout[old] || layout[id], style = Object.assign({}, styles[id] || {}, styles[old] || {});
        const pageContent = changes['__visual_page_content__:' + page + '|' + id];
        const content = pageContent !== undefined ? pageContent : (Object.prototype.hasOwnProperty.call(changes, id) ? changes[id] : changes[old]);
        if (el.tagName === 'IMG' && image) el.src = image;
        if (el.dataset.tierPrice && prices[el.dataset.tierPrice]) el.textContent = '€' + prices[el.dataset.tierPrice];
        if (position) el.style.transform = 'translate(' + position.x + 'px,' + position.y + 'px)';
        if (style && typeof style === 'object') Object.assign(el.style, style);
        if (content !== undefined && !['IMG','BODY','MAIN','SECTION','HEADER','FOOTER','DIV','ARTICLE','ASIDE','NAV','UL','OL'].includes(el.tagName)) el.innerHTML = safeHTML(content);
      });
      Object.keys(images).filter(id => id.startsWith('__visual_overlay__:') || id.startsWith('__visual_global_overlay__:')).forEach(id => {
        let img = document.querySelector('img[data-ve-overlay-key="' + id + '"]');
        if (!img) {
          img = document.createElement('img');
          img.alt = 'Neues Bild';
          img.className = 'visual-editor-added-image';
          img.dataset.veOverlayKey = id;
          document.body.appendChild(img);
        }
        img.src = images[id];
        const local = legacyKey(img), hidden = styles['__visual_page_hidden__:' + page + '|' + id], position = layout[local] || layout[id];
        Object.assign(img.style, styles[id] || {}, styles[local] || {}, hidden || {});
        if (position) img.style.transform = 'translate(' + position.x + 'px,' + position.y + 'px)';
      });
    })
    .catch(() => {});
})();

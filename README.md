// Cloudflare Worker: Production Video Player Proxy & Ad Stripper
// Handles: Vidplay / Echovideo, Vidmoly (Sub Omega), Gogoanime (Sub HD-1), and Byse/Moonwalk.

// Remove a `function NAME(...) { ... }` by brace matching and replace with a no-op.
function stripFunction(html, name) {
  const fnRe = new RegExp("function\\s+" + name + "\\s*\\(", "g");
  let match;
  while ((match = fnRe.exec(html)) !== null) {
    const start = match.index;
    const open = html.indexOf("{", fnRe.lastIndex);
    if (open === -1) break;
    let depth = 0;
    let i = open;
    for (; i < html.length; i++) {
      const ch = html[i];
      if (ch === "{") depth++;
      else if (ch === "}") { depth--; if (depth === 0) { i++; break; } }
    }
    const replacement = "function " + name + "() {}";
    html = html.slice(0, start) + replacement + html.slice(i);
    fnRe.lastIndex = start + replacement.length;
  }
  return html;
}

// Rewrite an upstream Set-Cookie into a worker-scoped cookie (browser stores under workers.dev).
function scopeCookie(raw) {
  if (!raw) return null;
  const c = raw.replace(/;\s*Domain=[^;]+/i, "").replace(/;\s*Path=[^;]+/i, "")
               .replace(/;\s*SameSite=[^;]+/i, "").replace(/;\s*Secure/i, "");
  return c + "; Path=/; SameSite=None; Secure";
}
function collectSetCookies(respHeaders) {
  const out = [];
  try {
    if (typeof respHeaders.getSetCookie === "function") {
      for (const sc of respHeaders.getSetCookie()) { const w = scopeCookie(sc); if (w) out.push(w); }
    } else {
      const sc = respHeaders.get("set-cookie");
      if (sc) { const w = scopeCookie(sc); if (w) out.push(w); }
    }
  } catch (e) {}
  return out;
}

// Rewrite an HLS playlist so every segment / child-playlist / URI= attribute routes through the worker.
function rewriteM3u8(text, base, workerPrefix) {
  return text.split("\n").map((line) => {
    const t = line.trim();
    if (!t) return line;
    if (t.charAt(0) === "#") {
      return line.replace(/URI="([^"]+)"/gi, (m, u) => {
        try { return 'URI="' + workerPrefix + encodeURIComponent(new URL(u, base).href) + '"'; }
        catch (e) { return m; }
      });
    }
    try {
      const abs = new URL(t, base).href;
      if (/^https?:/i.test(abs)) return workerPrefix + encodeURIComponent(abs);
    } catch (e) {}
    return line;
  }).join("\n");
}

export default {
  async fetch(request, env, ctx) {
    const reqUrl = new URL(request.url);
    let targetUrl = reqUrl.searchParams.get("url");

    const cookieHeader = request.headers.get("cookie") || "";
    let cookieOrigin = "";
    const cookieMatch = cookieHeader.match(/upstream_target=([^;]+)/);
    if (cookieMatch) { try { cookieOrigin = decodeURIComponent(cookieMatch[1]); } catch (e) {} }

    // 0. Resolve relative subresources when there is no explicit ?url=
    if (!targetUrl) {
      const referer = request.headers.get("referer") || "";
      if (referer.includes("url=")) {
        try {
          const parentUrl = new URL(referer).searchParams.get("url");
          if (parentUrl) {
            const parentObj = new URL(parentUrl);
            const parentOrigin = parentObj.origin;
            let basePath = parentObj.pathname.substring(0, parentObj.pathname.lastIndexOf('/') + 1) || '/';
            if (reqUrl.pathname.includes("getSources") && !reqUrl.pathname.includes("embed-")) {
              targetUrl = parentOrigin + basePath + reqUrl.pathname.replace(/^\//, '') + reqUrl.search;
            } else {
              targetUrl = new URL(reqUrl.pathname + reqUrl.search, parentOrigin).href;
            }
          }
        } catch (e) {}
      } else if (cookieOrigin) {
        targetUrl = new URL(reqUrl.pathname + reqUrl.search, cookieOrigin).href;
      }
    }

    if (!targetUrl) {
      return new Response("Missing 'url' query parameter", { status: 400 });
    }

    let parsedTarget;
    try {
      parsedTarget = new URL(targetUrl);
    } catch {
      return new Response("Invalid target URL", { status: 400 });
    }

    const host = parsedTarget.hostname.toLowerCase();
    const actualOrigin = parsedTarget.origin;
    const isByse = host.includes("bysesayeveum") || host.includes("mfw09") || host.includes("myvidplay");
    const workerPrefix = reqUrl.origin + "/?url=";
    let upstreamReferer = actualOrigin + "/";
    let upstreamOrigin = null;

    if (host.includes("gogoanime") || host.includes("megaplay")) {
      upstreamReferer = "https://gogoanime.me.uk/";
    } else if (host.includes("vidmoly")) {
      upstreamReferer = "https://vidmoly.biz/";
    } else if (host.includes("vmeas") || host.includes("vmpx")) {
      upstreamReferer = "https://vidmoly.biz/";
    } else if (host.includes("dpopdrop") || host.includes("roburnt") || host.includes("echovideo") || host.includes("play.echovideo.ru")) {
      if (parsedTarget.pathname.includes("getSources")) {
        const embedDir = parsedTarget.pathname.substring(0, parsedTarget.pathname.lastIndexOf('/') + 1) || '/';
        upstreamReferer = actualOrigin + embedDir;
        if (targetUrl.includes("id=%27") || targetUrl.includes("id='") || targetUrl.includes('id=%22') || targetUrl.includes('id="')) {
          targetUrl = targetUrl.replace(/id=(?:%27|'|%22|")([^&]+)/, 'id=$1');
          parsedTarget = new URL(targetUrl);
        }
      } else if (parsedTarget.pathname.includes("/cdn/")) {
        // Stream playlists and media chunks on roburnt10 / dpopdrop / echovideo
        upstreamReferer = "https://play.echovideo.ru/";
        upstreamOrigin = "https://play.echovideo.ru";
      } else {
        upstreamReferer = "https://aniwaves.ru/watch/ao-no-hako-season-2-82776/ep-1";
      }
    } else if (host.includes("magsrv") || host.includes("exoclick") || host.includes("surefootedpause")) {
      upstreamReferer = "https://9anime.cfd/";
      upstreamOrigin = "https://9anime.cfd";
    } else if (isByse) {
      upstreamReferer = "https://aniwaves.ru/watch/ao-no-hako-season-2-82776/ep-1";
    }

    const isStaticAsset = /\.(?:ts|m4s|mp4|webm|jpg|jpeg|png|webp|gif|svg|woff2?|ttf|otf|eot|css|js|ico)(?:$|\?)/i.test(parsedTarget.pathname) || parsedTarget.pathname.includes('/cdn/');
    const isImmutableChunk = /\.(?:ts|m4s|mp4|webm)(?:$|\?)/i.test(parsedTarget.pathname) || parsedTarget.pathname.includes('/cdn/');

    // Cloudflare Edge Cache API match (saves 90%+ of worker subrequests)
    const cache = caches.default;
    const cacheKey = new Request(reqUrl.origin + '/?url=' + encodeURIComponent(targetUrl), { method: 'GET' });

    if (request.method === 'GET' && isStaticAsset) {
      try {
        const cachedResp = await cache.match(cacheKey);
        if (cachedResp) {
          return cachedResp;
        }
      } catch (e) {}
    }

    const reqRange = request.headers.get("range");

    const upstreamHeaders = {
      "User-Agent": request.headers.get("user-agent") || "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      "Referer": upstreamReferer,
      "Accept": "*/*",
      "Accept-Language": request.headers.get("accept-language") || "en-US,en;q=0.9"
    };

    const clientIp = request.headers.get("cf-connecting-ip") || request.headers.get("x-forwarded-for");
    if (clientIp) {
      upstreamHeaders["X-Forwarded-For"] = clientIp;
      upstreamHeaders["CF-Connecting-IP"] = clientIp;
      upstreamHeaders["X-Real-IP"] = clientIp;
    }

    if (upstreamOrigin) {
      upstreamHeaders["Origin"] = upstreamOrigin;
    }

    if (reqRange) {
      upstreamHeaders["Range"] = reqRange;
    }

    if (isByse) {
      // Byse/Moonwalk whitelist headers + forward the browser's worker-scoped session cookies
      upstreamHeaders["Origin"] = "https://aniwaves.ru";
      upstreamHeaders["X-Embed-Origin"] = "aniwaves.ru";
      upstreamHeaders["X-Embed-Referer"] = "https://aniwaves.ru/watch/ao-no-hako-season-2-82776/ep-1";
      upstreamHeaders["X-Embed-Parent"] = "https://aniwaves.ru/watch/ao-no-hako-season-2-82776/ep-1";
      const fwdCookies = cookieHeader.split(/;\s*/).filter(Boolean)
        .filter(kv => !kv.startsWith("upstream_target=")).join("; ");
      if (fwdCookies) upstreamHeaders["Cookie"] = fwdCookies;
    }

    try {
      const upstreamResp = await fetch(targetUrl, {
        headers: upstreamHeaders,
        redirect: "follow",
        cf: isStaticAsset ? {
          cacheEverything: true,
          cacheTtl: isImmutableChunk ? 2592000 : 86400
        } : undefined
      });

      const contentType = upstreamResp.headers.get("content-type") || "";

      // 1. JSON responses (getSources output or embed APIs)
      if (contentType.includes("application/json") || parsedTarget.pathname.includes("getSources")) {
        let jsonText = await upstreamResp.text();
        try {
          const parsed = JSON.parse(jsonText);
          const wrap = u => (typeof u === 'string' && u.startsWith('http') && !u.includes(workerPrefix)) ? `${workerPrefix}${encodeURIComponent(u)}` : u;
          if (parsed && parsed.sources) {
            if (typeof parsed.sources === 'string') parsed.sources = wrap(parsed.sources);
            else if (Array.isArray(parsed.sources)) {
              parsed.sources = parsed.sources.map(s => {
                if (typeof s === 'string') return wrap(s);
                if (s && typeof s === 'object') {
                  if (s.file) s.file = wrap(s.file);
                  if (s.src) s.src = wrap(s.src);
                  if (s.url) s.url = wrap(s.url);
                }
                return s;
              });
            }
            jsonText = JSON.stringify(parsed);
          }
        } catch (e) {}
        const jsonHeaders = new Headers(upstreamResp.headers);
        jsonHeaders.delete("set-cookie");
        jsonHeaders.set("Content-Type", "application/json; charset=utf-8");
        jsonHeaders.set("Access-Control-Allow-Origin", "*");
        jsonHeaders.set("Access-Control-Allow-Credentials", "true");
        jsonHeaders.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
        jsonHeaders.set("Access-Control-Allow-Headers", "*");
        if (isByse) for (const sc of collectSetCookies(upstreamResp.headers)) jsonHeaders.append("Set-Cookie", sc);
        return new Response(jsonText, { status: upstreamResp.status, headers: jsonHeaders });
      }

      // 2. Non-HTML: media / stream / static assets
      if (!contentType.includes("text/html")) {
        const isM3u8Ext = /\.m3u8(?:$|\?)/i.test(parsedTarget.pathname + parsedTarget.search) || /mpegurl/i.test(contentType);
        // If content-type is image/jpeg or application/octet-stream, it could still be an HLS playlist (Vidplay obfuscation)
        if (isM3u8Ext) {
          const plText = await upstreamResp.text();
          const rewritten = rewriteM3u8(plText, targetUrl, workerPrefix);
          const mh = new Headers();
          mh.set("Content-Type", "application/vnd.apple.mpegurl");
          mh.set("Access-Control-Allow-Origin", "*");
          mh.set("Cache-Control", "public, max-age=60, s-maxage=60");
          return new Response(rewritten, { status: upstreamResp.status, headers: mh });
        }

        // Check if raw body starts with #EXTM3U (e.g. child playlists on roburnt10.store disguised as image/jpeg)
        const rawBuf = await upstreamResp.arrayBuffer();
        const headerSlice = new TextDecoder().decode(rawBuf.slice(0, 15));
        if (headerSlice.startsWith("#EXTM3U")) {
          const plText = new TextDecoder().decode(rawBuf);
          const rewritten = rewriteM3u8(plText, targetUrl, workerPrefix);
          const mh = new Headers();
          mh.set("Content-Type", "application/vnd.apple.mpegurl");
          mh.set("Access-Control-Allow-Origin", "*");
          mh.set("Cache-Control", "public, max-age=60, s-maxage=60");
          return new Response(rewritten, { status: upstreamResp.status, headers: mh });
        }

        // 1b. Generic passthrough for segments (.ts), images, icons with edge caching
        const passHeaders = new Headers(upstreamResp.headers);
        passHeaders.delete("Access-Control-Allow-Origin");
        passHeaders.delete("set-cookie");
        passHeaders.set("Access-Control-Allow-Origin", "*");
        passHeaders.delete("Content-Security-Policy");
        passHeaders.delete("X-Frame-Options");
        if (isImmutableChunk) {
          passHeaders.set("Cache-Control", "public, max-age=2592000, s-maxage=2592000, immutable");
        } else if (isStaticAsset) {
          passHeaders.set("Cache-Control", "public, max-age=86400, s-maxage=86400");
        }
        if (isByse) for (const sc of collectSetCookies(upstreamResp.headers)) passHeaders.append("Set-Cookie", sc);

        const finalResp = new Response(rawBuf, { status: upstreamResp.status, headers: passHeaders });
        if (isStaticAsset && upstreamResp.status === 200 && ctx && ctx.waitUntil) {
          ctx.waitUntil(cache.put(cacheKey, finalResp.clone()));
        }
        return finalResp;
      }

      // 2. Guard: an asset path that upstream answered with HTML is a real 404, not a document.
      const reqPath = parsedTarget.pathname.toLowerCase();
      if (/\.(?:js|mjs|css|woff2?|ttf|otf|eot|map|svg|png|jpe?g|webp|gif|ico)$/.test(reqPath)) {
        return new Response("Upstream asset unavailable", {
          status: 404,
          headers: { "Content-Type": "text/plain; charset=utf-8", "Access-Control-Allow-Origin": "*" }
        });
      }

      let html = await upstreamResp.text();

      // 2b. Gracefully handle dead or expired Echovideo / Vidplay embed pages (prevents Artplayer 404 fatal crash)
      if (host.includes("echovideo") && (upstreamResp.status >= 400 || html.includes("404-error") || html.includes("File not found"))) {
        const errorHtml = `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{margin:0;padding:0;background:#0b0914;color:#f8fafc;font-family:system-ui,-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;box-sizing:border-box;}
.box{max-width:440px;margin:20px;padding:28px 24px;border:1px solid rgba(139,92,246,0.3);border-radius:12px;background:rgba(18,15,30,0.85);box-shadow:0 8px 32px rgba(0,0,0,0.5);}
.icon{font-size:2.8rem;color:#f87171;margin-bottom:12px;display:block;}
h3{margin:0 0 8px 0;font-size:1.25rem;font-weight:700;}
p{margin:0 0 16px 0;color:#94a3b8;font-size:0.92rem;line-height:1.5;}
.sub-badge{display:inline-block;background:#5a2e98;color:#fff;padding:8px 18px;border-radius:6px;font-weight:600;font-size:0.88rem;text-decoration:none;cursor:pointer;}
</style></head><body>
<div class="box">
  <span class="icon">⚠️</span>
  <h3>Vidplay Stream Expired</h3>
  <p>The upstream token for this server is expired or unavailable. Switching to backup server...</p>
  <div class="sub-badge">Please select another server below if needed</div>
</div>
<script>
  try { window.parent.postMessage({ type: '9anime:stream-failed', reason: 'Vidplay 404' }, '*'); } catch(e){}
</script>
</body></html>`;
        return new Response(errorHtml, {
          status: 200,
          headers: {
            "Content-Type": "text/html; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "X-Frame-Options": "ALLOWALL"
          }
        });
      }

      // 3. Strip intrusive ad networks (external script tags + inline blobs)
      html = html.replace(/<script[^>]*src=["'][^"']*(?:adblockDetector|llvpn\.com|tag\.min\.js|monetag|vignette|profitablerate|clarity|yandex|statlytic|deskademeta)[^"']*["'][^>]*>\s*<\/script>/gi, "");
      html = html.replace(/<script[^>]*>(?:(?!<\/script>).)*?(?:llvpn\.com|tag\.min\.js|adblockDetector).*?<\/script>/gis, "");

      // 4. Vidmoly clean & de-ad
      if (host.includes("vidmoly")) {
        html = html.replace(/<script[^>]*>(?:(?!<\/script>).)*?(?:eval\(function\(p,a,c,k,e,d\)|mg_vd|vj_vs|yandex|mc\.yandex).*?<\/script>/gis, "");
        html = stripFunction(html, "lo_dlsm");
        html = stripFunction(html, "adBlockControl");
        html = stripFunction(html, "molyvastsc");
        html = stripFunction(html, "startDownload");
        html = html.replace(/\$\(['"]div\.video_ad['"]\)\.show\(\);/gi, "");
        html = html.replace(/var baseTracks\s*=\s*\[[\s\S]*?\];/gi, "var baseTracks = [];");
        html = html.replace(/var thumbTrackUrl\s*=\s*['"][^'"]*['"];/gi, "var thumbTrackUrl = '';");
        html = html.replace(/tracks:\s*baseTracks,?/gi, "tracks: [],");
        html = html.replace(/https:\/\/[a-zA-Z0-9.\-_]+\.vmpx\.online\/hls2\/([^?]+)\?([^"'\s]*srv=([^&"'\s]+)[^"'\s]*)/gi, "https://$3.vmeas.cloud/hls2/$1?$2");
        html = html.replace(/<div[^>]*class=["']dl-btn-container["'][^>]*>[\s\S]*?<\/div>/gis, "");
        html = html.replace("</head>", "<style>.dl-btn-container,#dl-btn-el{display:none !important;visibility:hidden !important;}</style></head>");
      }

      const embedDir = parsedTarget.pathname.substring(0, parsedTarget.pathname.lastIndexOf('/') + 1) || '/';

      // 5. Byse/Moonwalk: rewrite root-relative asset refs
      if (isByse) {
        html = html.replace(/(src|href)=["']\/(?!\/)([^"']+)["']/gi, (mm, attr, path) => {
          return `${attr}="${workerPrefix}${encodeURIComponent(actualOrigin + "/" + path)}"`;
        });
      }

      // 5b. Echovideo (Vidplay): rewrite relative scripts/styles & optimize Hls config
      if (host.includes("echovideo")) {
        // Replace buggy / incompatible Hls.js versions with rock-solid official Hls.js v1.5.8 (fixes seek freeze)
        html = html.replace(/<script[^>]*src=["'][^"']*(?:cdnbye|hls\.js)[^"']*["'][^>]*>\s*<\/script>/gi, '<script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.8/dist/hls.min.js"></script>');
        html = html.replace(/(src|href)=["']\/(?!\/)([^"']+)["']/gi, (mm, attr, path) => {
          return `${attr}="${actualOrigin}/${path}"`;
        });
        // Neutralize anti-iframe / frame-busting routine
        html = html.replace(/nE\(\)&&n1\[[\s\S]*?nt\(!\[\]\);/g, "/* neutralized frame-bust */");
        const ntIdx = html.indexOf("function nt(e){");
        if (ntIdx !== -1) {
          const ntEnd = html.indexOf(";function nZ(", ntIdx);
          if (ntEnd !== -1) {
            html = html.slice(0, ntIdx) + "function nt(e){} " + html.slice(ntEnd);
          }
        }

        // Optimize Hls constructor: larger buffer, extended timeouts for high-bitrate 1080p chunks, drift & gap tolerance
        html = html.replace(
          /const\s+engine\s*=\s*new\s+Hls\s*\(\s*\{[\s\S]*?\}\s*\);(?:\s*hls\s*=\s*engine;)?/,
          `const engine=new Hls({
            maxBufferLength: 60,
            maxMaxBufferLength: 120,
            backBufferLength: 60,
            maxBufferHole: 2.0,
            maxAudioFramesDrift: 500,
            stretchShortVideoTrack: true,
            fragLoadingTimeOut: 60000,
            fragLoadingMaxRetry: 8,
            fragLoadingRetryDelay: 1000,
            fragLoadingMaxRetryTimeout: 64000,
            levelLoadingTimeOut: 30000,
            manifestLoadingTimeOut: 30000,
            enableWorker: true,
            enableWebVTT: false,
            enableIMSC1: false,
            enableCEA708Captions: false,
            renderTextTracksNatively: false
          });
          hls=engine;window.__hlsEngine=engine;
          engine.on(Hls.Events.BUFFER_CREATED, function(e, data) {
            try {
              if (data.tracks) {
                if (data.tracks.audio) data.tracks.audio.buffer.mode = 'sequence';
                if (data.tracks.video) data.tracks.video.buffer.mode = 'sequence';
              }
            } catch(err) {}
          });`
        );

        // Seamless quality switching without flushing MSE buffers or freezing playback
        html = html.replace(
          /onSelect\s*\(\s*item\s*\)\s*\{\s*hls\.currentLevel\s*=\s*item\.index;\s*return\s+item\.html;\s*\}/,
          `onSelect(item){
            if(item.index===-1){
              hls.nextLevel=-1;
            } else {
              hls.nextLevel=item.index;
              hls.loadLevel=item.index;
            }
            return item.html;
          }`
        );
        html = html.replace(
          /engine\.on\s*\(\s*Hls\.Events\.LEVEL_SWITCHED\s*,\s*\(\)\s*=>\s*\{\s*if\s*\(!qualityReady\)\s*buildQualityMenu\(\)\s*\}\s*\);/,
          "engine.on(Hls.Events.LEVEL_SWITCHED,()=>{buildQualityMenu()});"
        );

        // Prevent resume seeking before the initial chunk has fully established the PTS timeline
        html = html.replace(
          /function\s+applyResume\s*\(\)\s*\{\s*if\s*\(!pendingSeek\)\s*return;/,
          `function applyResume(){if(!pendingSeek)return;
           const v_init=document.querySelector('video');
           if(!v_init||v_init.readyState<3||!v_init.buffered||v_init.buffered.length===0||v_init.buffered.end(0)<2.5)return;`
        );

        // Non-fatal Hls error handling: never abort in-flight fragment downloads during stalls
        html = html.replace(
          /console\.warn\('Artplayer HLS:'[\s\S]*?if\(!ev\?\.fatal\)return;/,
          `console.warn('Artplayer HLS:',ev?.type,ev?.details,'HTTP',status||'unknown','fatal',!!ev?.fatal);
           if(!ev?.fatal){
             if(ev?.details==='bufferSeekOverHole'){
               console.log('[Hls Recovery] Resolving seek hole');
               try{if(video&&!video.paused){video.currentTime+=0.1;}}catch(e){}
             }
             return;
           }`
        );

        html = html.replace("</head>", "<style>html,body,#mg-player{background:#000 !important;color:#fff !important;width:100% !important;height:100% !important;margin:0 !important;padding:0 !important;overflow:hidden !important;}</style></head>");
      }

      // 6a. Popunder / frame-bust shield
      const popunderShield = `
      <script>
        (function() {
          window.adsbygoogle = { loaded: true };
          window.open = function() { return { focus: function(){}, close: function(){} }; };
          document.addEventListener('click', function(e) {
            var target = e.target;
            while (target && target !== document.body && target !== document.documentElement) {
              if (target.tagName === 'A') {
                var tgt = target.getAttribute('target');
                var href = target.getAttribute('href') || '';
                if (tgt === '_blank' || href.startsWith('http://') || href.startsWith('https://')) {
                  if (!target.closest('#vplayer') && !target.closest('.jwplayer') && !target.closest('#root') && !target.closest('#player')) {
                    e.preventDefault(); e.stopPropagation(); return false;
                  }
                }
              }
              target = target.parentElement;
            }
          }, true);
          try {
            Object.defineProperty(window, 'top', { get: function() { return window.self; } });
            Object.defineProperty(window, 'parent', { get: function() { return window.self; } });
          } catch(err) {}
          window.alert = function() {};
          window.confirm = function() { return false; };
          window.prompt = function() { return null; };
        })();
      <\/script>
      `;

      // 6c. Byse/Moonwalk shield: proxy nested player iframe + route /api/ and /stream
      const byseShield = `
      <script>
        (function() {
          try {
            var WP = location.origin + "/?url=";
            var OA = "${actualOrigin}";
            var ED = "${embedDir}";
            var UP = ["bysesayeveum.com", "mfw09.org", "myvidplay.com", "echovideo.ru", "echovideo.to"];
            function isUp(h) { return UP.some(function(x){ return h.indexOf(x) !== -1; }) || /(?:dpopdrop|roburnt)\d*\.store/i.test(h); }
            function proxied(u) {
              try {
                var x = new URL(u, location.href);
                if (x.protocol === "data:" || x.protocol === "blob:") return u;
                if (isUp(x.hostname)) {
                  var already = (x.hostname === location.hostname && x.pathname === "/" && x.search.indexOf("url=") !== -1);
                  if (!already) return WP + encodeURIComponent(x.href);
                }
              } catch (e) {}
              return u;
            }
            var _f = window.fetch;
            window.fetch = function(u, o) {
              try {
                if (typeof u === "string") {
                  if (u.indexOf(WP) !== -1 || u.indexOf("/?url=") !== -1) {
                    return _f.apply(this, arguments);
                  }
                  if (u.indexOf("getSources") !== -1) {
                    var cleanPath = u.replace(/^.*?getSources/, 'getSources').replace(/^getSources\?id=(?:'|"|%27|%22)/, 'getSources?id=');
                    var full = OA + ED + cleanPath;
                    o = o || {}; try { o.credentials = "include"; } catch (e) {}
                    return _f.call(this, WP + encodeURIComponent(full), o);
                  }
                  if (u.indexOf("/api/") !== -1 || u.indexOf("/stream") !== -1) {
                    var full = /^https?:/i.test(u) ? u : OA + u;
                    o = o || {}; try { o.credentials = "include"; } catch (e) {}
                    return _f.call(this, WP + encodeURIComponent(full), o);
                  }
                  var x = new URL(u, location.href);
                  if (isUp(x.hostname) || (x.pathname && (x.pathname.endsWith(".m3u8") || x.pathname.includes("/cdn/") || x.pathname.endsWith(".ts")))) {
                    return _f.call(this, WP + encodeURIComponent(x.href), o);
                  }
                }
              } catch (e) {}
              return _f.apply(this, arguments);
            };
            var _o = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(m, u) {
              try {
                if (typeof u === "string") {
                  if (u.indexOf(WP) !== -1 || u.indexOf("/?url=") !== -1) {
                    return _o.apply(this, arguments);
                  }
                  var hit = u.indexOf("/api/") !== -1 || u.indexOf("/stream") !== -1 || u.indexOf("/cdn/") !== -1 || u.indexOf(".m3u8") !== -1 || u.indexOf(".ts") !== -1;
                  var x = null; try { x = new URL(u, location.href); } catch (e) {}
                  if (hit || (x && (isUp(x.hostname) || (x.pathname && (x.pathname.includes("/cdn/") || x.pathname.endsWith(".m3u8") || x.pathname.endsWith(".ts")))))) {
                    var full = /^https?:/i.test(u) ? u : (x ? x.href : OA + u);
                    var a = [].slice.call(arguments);
                    a[1] = WP + encodeURIComponent(full);
                    try { this.withCredentials = true; } catch (e) {}
                    return _o.apply(this, a);
                  }
                }
              } catch (e) {}
              return _o.apply(this, arguments);
            };
            function fix(f) { if (!f || f.tagName !== "IFRAME") return; try { var s = f.getAttribute("src"); if (s) { var p = proxied(s); if (p !== s) f.setAttribute("src", p); } } catch (e) {} }
            try {
              var d = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, "src");
              if (d && d.set) Object.defineProperty(HTMLIFrameElement.prototype, "src", { get: d.get, set: function(v) { d.set.call(this, proxied(String(v))); } });
            } catch (e) {}
            try {
              var mo = new MutationObserver(function(ms) {
                for (var i = 0; i < ms.length; i++) {
                  var mm = ms[i];
                  if (mm.type === "attributes" && mm.target && mm.target.tagName === "IFRAME") { fix(mm.target); continue; }
                  for (var j = 0; j < mm.addedNodes.length; j++) {
                    var n = mm.addedNodes[j]; if (!n || n.nodeType !== 1) continue; fix(n);
                    try { var a = n.getElementsByTagName ? n.getElementsByTagName("iframe") : []; for (var k = 0; k < a.length; k++) fix(a[k]); } catch (e) {}
                  }
                }
              });
              function go() { try { mo.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] }); } catch (e) {} }
              if (document.documentElement) go(); else document.addEventListener("DOMContentLoaded", go);
              document.addEventListener("DOMContentLoaded", function() { try { var a = document.getElementsByTagName("iframe"); for (var i = 0; i < a.length; i++) fix(a[i]); } catch (e) {} });
            } catch (e) {}
            try { Object.defineProperty(document, "referrer", { get: function() { return "https://aniwaves.ru/watch/ao-no-hako-season-2-82776/ep-1"; } }); } catch (e) {}
          } catch (e) {}
        })();
      <\/script>
      `;

      // 6d. Echovideo (Vidplay) seek/scrub auto-resume & timeline sync
      const echovideoShield = `
      <script>
        (function() {
          var wasPlayingBeforeSeek = false;

          function syncTimestampOffset(target) {
            try {
              if (window.__hlsEngine && window.__hlsEngine.bufferController) {
                var sb = window.__hlsEngine.bufferController.sourceBuffer;
                if (sb) {
                  if (sb.video && !sb.video.updating) sb.video.timestampOffset = target;
                  if (sb.audio && !sb.audio.updating) sb.audio.timestampOffset = target;
                }
              }
            } catch(e) {}
          }

          function attachVideoShield(video) {
            if (!video || video._shieldAttached) return;
            video._shieldAttached = true;

            video.addEventListener('play', function() {
              wasPlayingBeforeSeek = true;
            });
            video.addEventListener('pause', function() {
              if (!video.seeking) wasPlayingBeforeSeek = false;
            });

            video.addEventListener('seeking', function() {
              var target = video.currentTime;
              var isBuffered = false;
              for (var i = 0; i < video.buffered.length; i++) {
                if (target >= video.buffered.start(i) && target <= video.buffered.end(i)) {
                  isBuffered = true; break;
                }
              }
              if (!isBuffered) {
                syncTimestampOffset(target);
              }
            });

            video.addEventListener('seeked', function() {
              if (wasPlayingBeforeSeek && video.paused) {
                setTimeout(function() {
                  if (video.paused && wasPlayingBeforeSeek) {
                    video.play().catch(function(){});
                  }
                }, 100);
              }
            });
          }

          var checkVideo = setInterval(function() {
            var v = document.querySelector('video');
            if (v) attachVideoShield(v);
          }, 250);
        })();
      <\/script>
      `;

      let injected = popunderShield;
      if (isByse || host.includes("echovideo")) injected += byseShield;
      if (host.includes("echovideo")) injected += echovideoShield;

      if (html.includes("<head>")) {
        html = html.replace("<head>", "<head>" + injected);
      } else {
        html = injected + html;
      }

      const responseHeaders = new Headers();
      responseHeaders.set("Content-Type", "text/html; charset=utf-8");
      responseHeaders.set("Access-Control-Allow-Origin", "*");
      if (host.includes("echovideo")) {
        responseHeaders.set("Cache-Control", "no-cache, no-store, must-revalidate");
      } else {
        responseHeaders.set("Cache-Control", "public, max-age=1800, s-maxage=1800");
      }
      responseHeaders.set("X-Frame-Options", "ALLOWALL");
      responseHeaders.delete("Content-Security-Policy");
      responseHeaders.append("Set-Cookie", `upstream_target=${encodeURIComponent(actualOrigin)}; Path=/; SameSite=None; Secure; Max-Age=3600`);
      if (isByse) for (const sc of collectSetCookies(upstreamResp.headers)) responseHeaders.append("Set-Cookie", sc);

      const finalHtmlResp = new Response(html, { status: 200, headers: responseHeaders });
      if (!host.includes("echovideo") && ctx && ctx.waitUntil) {
        ctx.waitUntil(cache.put(cacheKey, finalHtmlResp.clone()));
      }
      return finalHtmlResp;

    } catch (err) {
      return new Response("Proxy Error: " + err.message, { status: 502 });
    }
  }
};

const CACHE = 'kadal-shell-v5';
const SHELL = ['/', '/index.html', '/styles.css', '/app.js', '/boundary.js', '/data/maritime-boundary.json', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png', '/vendor/leaflet.js', '/vendor/leaflet.css'];
self.addEventListener('install', e => {e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL))); self.skipWaiting();});
self.addEventListener('activate', e => e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // Never cache API data or map tiles. An offline shell must not imply live advice.
  if(e.request.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/'))return;
  e.respondWith(fetch(e.request).then(response=>{
    if(response.ok){const copy=response.clone();caches.open(CACHE).then(c=>c.put(e.request,copy));}
    return response;
  }).catch(()=>caches.match(e.request).then(cached=>cached||(e.request.mode==='navigate'?caches.match('/'):Response.error()))));
});

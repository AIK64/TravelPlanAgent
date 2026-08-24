import { useEffect, useRef } from "react";

declare global { interface Window { AMap?: any; _AMapSecurityConfig?: { securityJsCode: string } } }

export type MapPoint = { name: string; longitude: number; latitude: number };

export function MapPanel({ points }: { points: MapPoint[] }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const key = import.meta.env.VITE_AMAP_JS_KEY;
    if (!key || !container.current) return;
    const security = import.meta.env.VITE_AMAP_JS_SECURITY_CODE;
    if (security) window._AMapSecurityConfig = { securityJsCode: security };
    const render = () => {
      if (!container.current || !window.AMap) return;
      const map = new window.AMap.Map(container.current, { zoom: 11, viewMode: "2D" });
      const markers = points.map(point => new window.AMap.Marker({ position: [point.longitude, point.latitude], title: point.name }));
      map.add(markers); if (markers.length) map.setFitView(markers, false, [48, 48, 48, 48]);
    };
    if (window.AMap) { render(); return; }
    const script = document.createElement("script");
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}`;
    script.onload = render; document.head.appendChild(script);
  }, [points]);
  return <section className="panel map-panel">
    <div className="panel-title"><span>行程地图</span><small>GCJ-02</small></div>
    <div ref={container} className="map-canvas">{!import.meta.env.VITE_AMAP_JS_KEY && <div className="map-placeholder">配置 VITE_AMAP_JS_KEY 后展示 POI 与路线<br/><small>浏览器 Key 与服务端 Web Service Key 必须分离</small></div>}</div>
  </section>;
}

import { useEffect, useRef, useState, useCallback } from "react";

export default function useWebSocket(url = "/ws") {
  const wsRef = useRef(null);
  const [lastMessage, setLastMessage] = useState(null);
  const [isConnected, setIsConnected] = useState(false);
  const reconnectTimeout = useRef(null);

  const connect = useCallback(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = url.startsWith("ws") ? url : `${protocol}//${window.location.host}${url}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      // Heartbeat
      const interval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 30000);
      ws._heartbeat = interval;
    };

    ws.onmessage = (event) => {
      if (event.data === "pong") return;
      try {
        const data = JSON.parse(event.data);
        setLastMessage(data);
      } catch {}
    };

    ws.onclose = () => {
      setIsConnected(false);
      if (ws._heartbeat) clearInterval(ws._heartbeat);
      // Reconnect after 3s
      reconnectTimeout.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => ws.close();
  }, [url]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimeout.current) clearTimeout(reconnectTimeout.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  return { lastMessage, isConnected };
}

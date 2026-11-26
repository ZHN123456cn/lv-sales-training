/**
 * SSE 流式聊天客户端。
 *
 * 由于 EventSource 只支持 GET，而聊天接口需要 POST 发送消息体，
 * 这里使用 fetch + ReadableStream 手动解析 text/event-stream。
 *
 * 回调：
 *   onToken(content)         — 收到流式文本 token
 *   onToolCall({name, arguments, call_id})
 *   onToolResult({name, result, truncated})
 *   onDone({session_id})     — 流结束
 *   onError(message)         — 出错
 *   signal                   — AbortSignal，可用于中断
 */

export async function streamChat({ message, sessionId, onToken, onToolCall, onToolResult, onDone, onError, signal }) {
  try {
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId || null }),
      signal,
    })

    if (!res.ok) {
      let detail = res.statusText
      try {
        const err = await res.json()
        detail = err.detail || err.message || detail
      } catch {
        // ignore
      }
      throw new Error(detail)
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      // SSE 事件以 \n\n 分隔
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''

      for (const part of parts) {
        if (!part.trim()) continue
        const lines = part.split('\n')
        let event = 'message'
        let data = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) event = line.slice(7)
          else if (line.startsWith('data: ')) data += line.slice(6)
        }
        if (!data) continue
        let payload = {}
        try {
          payload = JSON.parse(data)
        } catch {
          payload = { raw: data }
        }

        switch (event) {
          case 'token':
            onToken?.(payload.content || '')
            break
          case 'tool_call':
            onToolCall?.(payload)
            break
          case 'tool_result':
            onToolResult?.(payload)
            break
          case 'done':
            onDone?.(payload)
            break
          case 'error':
            onError?.(payload.message || '未知错误')
            break
        }
      }
    }
  } catch (err) {
    if (err.name === 'AbortError') return
    onError?.(err.message || '网络错误')
  }
}

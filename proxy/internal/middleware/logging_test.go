package middleware

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestLoggingPreservesStreamingResponseCapabilities(t *testing.T) {
	const writeTimeout = 100 * time.Millisecond
	const terminalDelay = 250 * time.Millisecond

	handler := Logging(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if err := http.NewResponseController(w).SetWriteDeadline(time.Time{}); err != nil {
			http.Error(w, fmt.Sprintf("clear write deadline: %v", err), http.StatusInternalServerError)
			return
		}

		flusher, ok := w.(http.Flusher)
		if !ok {
			http.Error(w, "streaming response writer does not support flushing", http.StatusInternalServerError)
			return
		}

		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"type\":\"message_start\"}\n\n")
		flusher.Flush()
		time.Sleep(terminalDelay)
		fmt.Fprint(w, "data: {\"type\":\"message_stop\"}\n\n")
		flusher.Flush()
	}))

	server := httptest.NewUnstartedServer(handler)
	server.Config.WriteTimeout = writeTimeout
	server.Start()
	defer server.Close()

	client := server.Client()
	client.Timeout = time.Second
	response, err := client.Get(server.URL)
	if err != nil {
		t.Fatalf("streaming request failed: %v", err)
	}
	defer response.Body.Close()

	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatalf("read streaming response: %v", err)
	}
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want %d; body=%q", response.StatusCode, http.StatusOK, body)
	}
	if !strings.Contains(string(body), "message_stop") {
		t.Fatalf("streaming response missing terminal event: body=%q", body)
	}
}

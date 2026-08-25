package handler

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/seifghazi/claude-code-monitor/internal/model"
)

type deadlineWriter struct {
	*httptest.ResponseRecorder
	deadline time.Time
	err      error
}

func (w *deadlineWriter) SetWriteDeadline(deadline time.Time) error {
	w.deadline = deadline
	return w.err
}

func TestPrepareStreamWriteDeadline(t *testing.T) {
	t.Run("non-stream leaves writer untouched", func(t *testing.T) {
		w := &deadlineWriter{ResponseRecorder: httptest.NewRecorder()}
		if err := prepareStreamWriteDeadline(w, false); err != nil {
			t.Fatalf("prepareStreamWriteDeadline() error = %v", err)
		}
		if !w.deadline.IsZero() {
			t.Fatalf("deadline = %v, want zero value untouched", w.deadline)
		}
	})

	t.Run("stream clears deadline", func(t *testing.T) {
		w := &deadlineWriter{ResponseRecorder: httptest.NewRecorder(), deadline: time.Now()}
		if err := prepareStreamWriteDeadline(w, true); err != nil {
			t.Fatalf("prepareStreamWriteDeadline() error = %v", err)
		}
		if !w.deadline.IsZero() {
			t.Fatalf("deadline = %v, want zero", w.deadline)
		}
	})

	t.Run("unsupported writer fails closed", func(t *testing.T) {
		err := prepareStreamWriteDeadline(httptest.NewRecorder(), true)
		if !errors.Is(err, errStreamWriteDeadlineUnsupported) {
			t.Fatalf("error = %v, want %v", err, errStreamWriteDeadlineUnsupported)
		}
	})
}

func TestMessagesUnsupportedDeadlineFailsBeforeRoutingStorageOrUpstream(t *testing.T) {
	body := []byte(`{"model":"gpt-5.6-luna","max_tokens":16,"messages":[],"stream":true}`)
	request := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(string(body)))
	request = request.WithContext(context.WithValue(request.Context(), model.BodyBytesKey, body))
	recorder := httptest.NewRecorder()

	// All Handler dependencies are intentionally nil. Reaching routing, storage,
	// or an upstream provider would panic and fail this test.
	(&Handler{}).Messages(recorder, request)

	if recorder.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusInternalServerError)
	}
	if !strings.Contains(recorder.Body.String(), streamWriteDeadlineErrorCode) {
		t.Fatalf("body = %q, want stable error %q", recorder.Body.String(), streamWriteDeadlineErrorCode)
	}
}

func TestStreamWriteTimeoutOldFailsNewPasses(t *testing.T) {
	const writeTimeout = 100 * time.Millisecond
	const terminalDelay = 250 * time.Millisecond

	oldBody, oldErr := runDeadlineServer(t, writeTimeout, terminalDelay, false, false)
	if oldErr == nil && strings.Contains(oldBody, "message_stop") {
		t.Fatalf("old path unexpectedly reached terminal: body=%q", oldBody)
	}

	newBody, newErr := runDeadlineServer(t, writeTimeout, terminalDelay, true, false)
	if newErr != nil {
		t.Fatalf("new stream path failed: %v (body=%q)", newErr, newBody)
	}
	if !strings.Contains(newBody, "message_stop") {
		t.Fatalf("new stream path missing terminal: body=%q", newBody)
	}

	nonStreamBody, nonStreamErr := runDeadlineServer(t, writeTimeout, terminalDelay, true, true)
	if nonStreamErr == nil && strings.Contains(nonStreamBody, "message_stop") {
		t.Fatalf("non-stream path unexpectedly escaped server deadline: body=%q", nonStreamBody)
	}
}

func TestStreamWriteDeadlineCancellation(t *testing.T) {
	ctxCanceled := make(chan struct{})
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := prepareStreamWriteDeadline(w, true); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"type\":\"message_start\"}\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
		close(ctxCanceled)
	})

	server, address := startDeadlineServer(t, 100*time.Millisecond, handler)
	defer stopDeadlineServer(t, server)

	ctx, cancel := context.WithCancel(context.Background())
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://"+address, nil)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	reader := bufio.NewReader(resp.Body)
	if _, err := reader.ReadString('\n'); err != nil {
		t.Fatalf("read first event: %v", err)
	}
	cancel()
	_ = resp.Body.Close()

	select {
	case <-ctxCanceled:
	case <-time.After(time.Second):
		t.Fatal("server request context was not canceled within one second")
	}
}

func TestStreamWriteTimeout610SecondReleaseGate(t *testing.T) {
	if os.Getenv("CCP_LONG_WRITE_TIMEOUT_TEST") != "1" {
		t.Skip("set CCP_LONG_WRITE_TIMEOUT_TEST=1 to run the 610-second release gate")
	}
	const writeTimeout = 10 * time.Minute
	const terminalDelay = 610 * time.Second

	t.Run("old path fails", func(t *testing.T) {
		t.Parallel()
		body, err := runDeadlineServer(t, writeTimeout, terminalDelay, false, false)
		if err == nil && strings.Contains(body, "message_stop") {
			t.Fatal("old path unexpectedly reached terminal after 610 seconds")
		}
	})
	t.Run("new path passes", func(t *testing.T) {
		t.Parallel()
		body, err := runDeadlineServer(t, writeTimeout, terminalDelay, true, false)
		if err != nil || !strings.Contains(body, "message_stop") {
			t.Fatalf("new path failed 610-second gate: err=%v terminal=%v", err, strings.Contains(body, "message_stop"))
		}
	})
}

func runDeadlineServer(t *testing.T, writeTimeout, terminalDelay time.Duration, clear, nonStream bool) (string, error) {
	t.Helper()
	handler := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if err := prepareStreamWriteDeadline(w, clear && !nonStream); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: {\"type\":\"message_start\"}\n\n")
		w.(http.Flusher).Flush()
		heartbeat := time.NewTicker(10 * time.Second)
		defer heartbeat.Stop()
		timer := time.NewTimer(terminalDelay)
		defer timer.Stop()
		for {
			select {
			case <-heartbeat.C:
				fmt.Fprint(w, "data: {\"type\":\"ping\"}\n\n")
				w.(http.Flusher).Flush()
			case <-timer.C:
				fmt.Fprint(w, "data: {\"type\":\"message_stop\"}\n\n")
				w.(http.Flusher).Flush()
				return
			}
		}
	})
	server, address := startDeadlineServer(t, writeTimeout, handler)
	defer stopDeadlineServer(t, server)

	client := &http.Client{Timeout: terminalDelay + 30*time.Second}
	resp, err := client.Get("http://" + address)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	return string(body), err
}

func startDeadlineServer(t *testing.T, writeTimeout time.Duration, handler http.Handler) (*http.Server, string) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: handler, WriteTimeout: writeTimeout}
	go func() {
		if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			t.Errorf("server.Serve() error = %v", err)
		}
	}()
	return server, listener.Addr().String()
}

func stopDeadlineServer(t *testing.T, server *http.Server) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		t.Fatalf("server.Shutdown() error = %v", err)
	}
}

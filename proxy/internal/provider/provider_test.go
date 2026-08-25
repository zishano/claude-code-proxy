package provider

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/seifghazi/claude-code-monitor/internal/config"
)

const anthropicRequestBody = `{
  "model": "gpt-5.6-luna",
  "max_tokens": 1024,
  "stream": true,
  "system": [{"type":"text","text":"system","cache_control":{"type":"ephemeral"}}],
  "messages": [{"role":"user","content":"use the tool"}],
  "tools": [{"name":"Read","description":"read a file","input_schema":{"type":"object","properties":{"file_path":{"type":"string"}},"required":["file_path"]}}],
  "tool_choice": {"type":"tool","name":"Read"}
}`

const toolUseSSE = "event: message_start\n" +
	"data: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_test\",\"type\":\"message\",\"role\":\"assistant\",\"model\":\"gpt-5.6-luna\",\"content\":[],\"stop_reason\":null}}\n\n" +
	"event: content_block_start\n" +
	"data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"tool_use\",\"id\":\"toolu_test\",\"name\":\"Read\",\"input\":{}}}\n\n" +
	"event: content_block_delta\n" +
	"data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"input_json_delta\",\"partial_json\":\"{\\\"file_path\\\":\\\"README.md\\\"}\"}}\n\n" +
	"event: content_block_stop\n" +
	"data: {\"type\":\"content_block_stop\",\"index\":0}\n\n" +
	"event: message_delta\n" +
	"data: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"tool_use\",\"stop_sequence\":null},\"usage\":{\"output_tokens\":8}}\n\n" +
	"event: message_stop\n" +
	"data: {\"type\":\"message_stop\"}\n\n"

func TestProviderHTTPClientsHaveNoAbsoluteTimeout(t *testing.T) {
	anthropic := NewAnthropicProvider(&config.AnthropicProviderConfig{}).(*AnthropicProvider)
	if anthropic.client.Timeout != 0 {
		t.Errorf("Anthropic client timeout = %s, want 0", anthropic.client.Timeout)
	}

	openAI := NewOpenAIProvider(&config.OpenAIProviderConfig{}).(*OpenAIProvider)
	if openAI.client.Timeout != 0 {
		t.Errorf("OpenAI client timeout = %s, want 0", openAI.client.Timeout)
	}
}

func TestAnthropicProviderPreservesMessagesProtocol(t *testing.T) {
	tests := []struct {
		name        string
		stream      bool
		response    string
		contentType string
	}{
		{
			name:        "non-streaming tool use",
			stream:      false,
			response:    `{"id":"msg_test","type":"message","role":"assistant","model":"gpt-5.6-luna","content":[{"type":"tool_use","id":"toolu_test","name":"Read","input":{"file_path":"README.md"}}],"stop_reason":"tool_use","usage":{"input_tokens":10,"output_tokens":8}}`,
			contentType: "application/json",
		},
		{
			name:        "streaming tool use",
			stream:      true,
			response:    toolUseSSE,
			contentType: "text/event-stream",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var attempts int32
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				atomic.AddInt32(&attempts, 1)
				if r.URL.Path != "/api/v1/messages" {
					t.Errorf("path = %q, want /api/v1/messages", r.URL.Path)
				}
				if r.URL.RawQuery != "beta=tools" {
					t.Errorf("query = %q, want beta=tools", r.URL.RawQuery)
				}
				if got := r.Header.Get("Authorization"); got != "Bearer consumer-test" {
					t.Errorf("Authorization = %q", got)
				}
				if got := r.Header.Get("anthropic-version"); got != "2023-06-01" {
					t.Errorf("anthropic-version = %q", got)
				}

				body, err := io.ReadAll(r.Body)
				if err != nil {
					t.Errorf("read request body: %v", err)
				}
				for _, fragment := range []string{
					`"model": "gpt-5.6-luna"`,
					`"cache_control":{"type":"ephemeral"}`,
					`"tools":`,
					`"tool_choice":`,
				} {
					if !strings.Contains(string(body), fragment) {
						t.Errorf("request body missing %q", fragment)
					}
				}

				w.Header().Set("Content-Type", tt.contentType)
				_, _ = io.WriteString(w, tt.response)
			}))
			defer upstream.Close()

			provider := NewAnthropicProvider(&config.AnthropicProviderConfig{
				BaseURL: upstream.URL + "/api",
				Version: "2023-06-01",
			}).(*AnthropicProvider)
			body := strings.Replace(anthropicRequestBody, `"stream": true`, fmt.Sprintf(`"stream": %t`, tt.stream), 1)
			req := httptest.NewRequest(http.MethodPost, "http://ccp.invalid/v1/messages?beta=tools", strings.NewReader(body))
			req.Header.Set("Authorization", "Bearer consumer-test")
			req.Header.Set("Content-Type", "application/json")
			resp, err := provider.ForwardRequest(context.Background(), req)
			if err != nil {
				t.Fatalf("ForwardRequest() error = %v", err)
			}
			defer resp.Body.Close()

			gotBody, err := io.ReadAll(resp.Body)
			if err != nil {
				t.Fatalf("read response body: %v", err)
			}
			if string(gotBody) != tt.response {
				t.Fatalf("response body changed:\n got: %q\nwant: %q", gotBody, tt.response)
			}
			if got := atomic.LoadInt32(&attempts); got != 1 {
				t.Fatalf("upstream attempts = %d, want 1", got)
			}
		})
	}
}

func TestProviderRequestContextCancellation(t *testing.T) {
	tests := []struct {
		name string
		new  func(string) Provider
	}{
		{
			name: "anthropic",
			new: func(baseURL string) Provider {
				return NewAnthropicProvider(&config.AnthropicProviderConfig{BaseURL: baseURL, Version: "2023-06-01"})
			},
		},
		{
			name: "openai",
			new: func(baseURL string) Provider {
				return NewOpenAIProvider(&config.OpenAIProviderConfig{BaseURL: baseURL, APIKey: "test"})
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var attempts int32
			var startedOnce sync.Once
			started := make(chan struct{})
			release := make(chan struct{})
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				atomic.AddInt32(&attempts, 1)
				startedOnce.Do(func() { close(started) })
				<-release
			}))
			defer func() {
				close(release)
				upstream.Close()
			}()

			ctx, cancel := context.WithCancel(context.Background())
			req := httptest.NewRequest(http.MethodPost, "http://ccp.invalid/v1/messages", strings.NewReader(anthropicRequestBody)).WithContext(ctx)
			result := make(chan error, 1)
			go func() {
				_, err := tt.new(upstream.URL).ForwardRequest(ctx, req)
				result <- err
			}()

			select {
			case <-started:
			case <-time.After(time.Second):
				t.Fatal("upstream request did not start")
			}

			cancelledAt := time.Now()
			cancel()
			select {
			case err := <-result:
				if err == nil {
					t.Fatal("ForwardRequest() returned nil error after cancellation")
				}
				if !errors.Is(err, context.Canceled) {
					t.Fatalf("error = %v, want context.Canceled", err)
				}
				if elapsed := time.Since(cancelledAt); elapsed > time.Second {
					t.Fatalf("cancellation took %s, want <= 1s", elapsed)
				}
			case <-time.After(time.Second):
				t.Fatal("provider did not stop within 1s of context cancellation")
			}
			if got := atomic.LoadInt32(&attempts); got != 1 {
				t.Fatalf("upstream attempts = %d, want 1", got)
			}
		})
	}
}

func TestAnthropicProviderLongStreamingExchange(t *testing.T) {
	if os.Getenv("CCP_LONG_SSE_TEST") != "1" {
		t.Skip("set CCP_LONG_SSE_TEST=1 to run the 305-second release gate")
	}

	const streamDuration = 305 * time.Second
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		flusher, ok := w.(http.Flusher)
		if !ok {
			t.Error("test server does not support flushing")
			return
		}

		_, _ = io.WriteString(w, "event: message_start\ndata: {\"type\":\"message_start\"}\n\n")
		flusher.Flush()
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		timer := time.NewTimer(streamDuration)
		defer timer.Stop()

		for {
			select {
			case <-r.Context().Done():
				return
			case <-ticker.C:
				if _, err := io.WriteString(w, ": heartbeat\n\n"); err != nil {
					return
				}
				flusher.Flush()
			case <-timer.C:
				_, _ = io.WriteString(w, "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n")
				flusher.Flush()
				return
			}
		}
	}))
	defer upstream.Close()

	provider := NewAnthropicProvider(&config.AnthropicProviderConfig{
		BaseURL: upstream.URL,
		Version: "2023-06-01",
	}).(*AnthropicProvider)
	req := httptest.NewRequest(http.MethodPost, "http://ccp.invalid/v1/messages", strings.NewReader(anthropicRequestBody))
	startedAt := time.Now()
	resp, err := provider.ForwardRequest(context.Background(), req)
	if err != nil {
		t.Fatalf("ForwardRequest() error = %v", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read streaming body after %s: %v", time.Since(startedAt), err)
	}
	if elapsed := time.Since(startedAt); elapsed < streamDuration {
		t.Fatalf("stream ended after %s, want at least %s", elapsed, streamDuration)
	}
	if !strings.Contains(string(body), `"type":"message_stop"`) {
		t.Fatalf("final message_stop missing from %d-byte stream", len(body))
	}
}

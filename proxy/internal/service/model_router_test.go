package service

import (
	"context"
	"log"
	"net/http"
	"os"
	"testing"

	"github.com/seifghazi/claude-code-monitor/internal/config"
	"github.com/seifghazi/claude-code-monitor/internal/model"
	"github.com/seifghazi/claude-code-monitor/internal/provider"
)

type testProvider string

func (p testProvider) Name() string {
	return string(p)
}

func (p testProvider) ForwardRequest(context.Context, *http.Request) (*http.Response, error) {
	panic("test provider must not forward requests")
}

func TestModelRouter_ProviderSelection(t *testing.T) {
	cfg := &config.Config{
		Subagents: config.SubagentsConfig{
			Enable:   false,
			Mappings: map[string]string{},
		},
	}
	providers := map[string]provider.Provider{
		"anthropic": testProvider("anthropic"),
		"openai":    testProvider("openai"),
	}
	router := NewModelRouter(cfg, providers, log.New(os.Stdout, "test: ", 0))

	tests := []struct {
		model        string
		wantProvider string
	}{
		{model: "gpt-5.6-luna", wantProvider: "anthropic"},
		{model: "gpt-5.6-terra", wantProvider: "anthropic"},
		{model: "gpt-4o", wantProvider: "anthropic"},
		{model: "gpt-custom-test", wantProvider: "anthropic"},
		{model: "o1", wantProvider: "openai"},
		{model: "o1-mini", wantProvider: "openai"},
		{model: "o3", wantProvider: "openai"},
		{model: "o3-pro", wantProvider: "openai"},
		{model: "claude-3-opus-20240229", wantProvider: "anthropic"},
		{model: "unknown-model", wantProvider: "anthropic"},
	}

	for _, tt := range tests {
		t.Run(tt.model, func(t *testing.T) {
			decision, err := router.DetermineRoute(&model.AnthropicRequest{Model: tt.model})
			if err != nil {
				t.Fatalf("DetermineRoute() error = %v", err)
			}
			if got := decision.Provider.Name(); got != tt.wantProvider {
				t.Fatalf("provider = %q, want %q", got, tt.wantProvider)
			}
			if decision.OriginalModel != tt.model {
				t.Errorf("OriginalModel = %q, want %q", decision.OriginalModel, tt.model)
			}
			if decision.TargetModel != tt.model {
				t.Errorf("TargetModel = %q, want %q", decision.TargetModel, tt.model)
			}
		})
	}
}

func TestModelRouter_EdgeCases(t *testing.T) {
	// Setup
	cfg := &config.Config{
		Subagents: config.SubagentsConfig{
			Mappings: map[string]string{
				"streaming-systems-engineer": "gpt-4o",
			},
		},
	}

	providers := make(map[string]provider.Provider)
	providers["anthropic"] = nil
	providers["openai"] = nil

	logger := log.New(os.Stdout, "test: ", log.LstdFlags)
	router := NewModelRouter(cfg, providers, logger)

	tests := []struct {
		name          string
		request       *model.AnthropicRequest
		expectedRoute string
		expectedModel string
		description   string
	}{
		{
			name: "Regular Claude Code request (no Notes section)",
			request: &model.AnthropicRequest{
				Model: "claude-3-opus-20240229",
				System: []model.AnthropicSystemMessage{
					{Text: "You are Claude Code, Anthropic's official CLI for Claude."},
					{Text: "You are an interactive CLI tool that helps users with software engineering tasks. Use the instructions below and the tools available to you to assist the user."},
				},
			},
			expectedRoute: "anthropic",
			expectedModel: "claude-3-opus-20240229",
			description:   "Regular Claude Code requests should use original model",
		},
		{
			name: "Non-Claude Code request",
			request: &model.AnthropicRequest{
				Model: "claude-3-opus-20240229",
				System: []model.AnthropicSystemMessage{
					{Text: "You are a helpful assistant."},
				},
			},
			expectedRoute: "anthropic",
			expectedModel: "claude-3-opus-20240229",
			description:   "Non-Claude Code requests should use original model",
		},
		{
			name: "Single system message",
			request: &model.AnthropicRequest{
				Model:  "claude-3-opus-20240229",
				System: []model.AnthropicSystemMessage{},
			},
			expectedRoute: "anthropic",
			expectedModel: "claude-3-opus-20240229",
			description:   "Requests with no system messages should use original model",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if len(tt.request.System) == 2 {
				// Test extract static prompt for second message
				fullPrompt := tt.request.System[1].Text
				staticPrompt := router.extractStaticPrompt(fullPrompt)

				// Verify no "Notes:" in static prompt
				if contains(staticPrompt, "Notes:") {
					t.Errorf("Static prompt should not contain 'Notes:' section")
				}
			}

			// Log for manual verification
			t.Logf("Test case: %s", tt.description)
		})
	}
}

func TestModelRouter_ExtractStaticPrompt(t *testing.T) {
	router := &ModelRouter{}

	tests := []struct {
		name     string
		input    string
		expected string
	}{
		{
			name:     "Prompt with Notes section",
			input:    "You are an expert engineer.\n\nNotes:\n- Some dynamic content\n- More notes",
			expected: "You are an expert engineer.",
		},
		{
			name:     "Prompt without Notes section",
			input:    "You are an expert engineer.\nNo notes here.",
			expected: "You are an expert engineer.\nNo notes here.",
		},
		{
			name:     "Prompt with double newline before Notes",
			input:    "You are an expert.\n\nNotes:\nDynamic content",
			expected: "You are an expert.",
		},
		{
			name:     "Empty prompt",
			input:    "",
			expected: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := router.extractStaticPrompt(tt.input)
			if result != tt.expected {
				t.Errorf("extractStaticPrompt() = %q, want %q", result, tt.expected)
			}
		})
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(substr) == 0 ||
		(len(s) > 0 && len(substr) > 0 && s[0:len(substr)] == substr) ||
		(len(s) > len(substr) && contains(s[1:], substr)))
}

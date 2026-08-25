package service

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

// TraceEntry 单条请求的 trace 观测。
// 能采集到的字段填真实值;代理不产生的字段置 null。
type TraceEntry struct {
	T           float64  `json:"t"`             // 相对会话起点的秒数
	Model       string   `json:"model"`         // 请求使用的模型(路由后)
	In          int      `json:"in"`            // 输入 Token 数
	Out         int      `json:"out"`           // 输出 Token 数
	HashIDs     []int    `json:"hash_ids"`      // Prompt 分块 ID(代理不产, null)
	APITime     float64  `json:"api_time"`      // 请求总耗时(秒)
	ThinkTime   *float64 `json:"think_time"`    // 思考时间(代理不测, null)
	Type        string   `json:"type"`          // "s"=流式 / "n"=非流式
	TTFT        float64  `json:"ttft"`          // 首 Token 延迟(仅流式可观测)
	Status      int      `json:"status"`        // HTTP 状态码
	BlockSize   *int     `json:"block_size"`    // Prompt 分块大小(代理不产, null)
	HashIDScope *string  `json:"hash_id_scope"` // 块 ID 作用域(代理不产, null)
}

// TraceUpdate 一份累积的观测快照(对齐 cc-traces 顶层结构)。
type Trace struct {
	ID          string       `json:"id"`
	Models      []string     `json:"models"`
	BlockSize   *int         `json:"block_size"`
	HashIDScope *string      `json:"hash_id_scope"`
	Requests    []TraceEntry `json:"requests"`
}

// traceSession 单个会话的累积状态。
type traceSession struct {
	traceID   string
	startTime time.Time
	models    map[string]bool
	requests  []TraceEntry
}

// TraceService 按会话累积并输出 trace 观测。
type TraceService struct {
	mu       sync.Mutex
	sessions map[string]*traceSession // key:会话标识(如 metadata.conversation_id)
	filePath string
	ordered  []string // 记录会话创建顺序,便于 trace.jsonl 输出
	logger   *log.Logger
}

func NewTraceService(path string, logger *log.Logger) *TraceService {
	if path == "" {
		path = filepath.Join("trace", "trace.jsonl")
	}
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		_ = os.MkdirAll(dir, 0o755)
	}
	return &TraceService{
		sessions: make(map[string]*traceSession),
		filePath: path,
		logger:   logger,
	}
}

// Add 记录一次真实请求观测到指定会话。
func (t *TraceService) Add(sessionKey, model string, in, out int, apiTimeMillis, ttftMillis float64, isStreaming bool, status int, requestID string) {
	t.mu.Lock()
	defer t.mu.Unlock()

	if sessionKey == "" {
		sessionKey = "default"
	}
	sess := t.sessions[sessionKey]
	if sess == nil {
		sess = &traceSession{
			traceID:   newTraceID(),
			startTime: time.Now(),
			models:    make(map[string]bool),
		}
		t.sessions[sessionKey] = sess
		t.ordered = append(t.ordered, sessionKey)
	}

	name := "n"
	if isStreaming {
		name = "s"
	}
	var ttft float64
	if isStreaming && ttftMillis >= 0 {
		ttft = ttftMillis / 1000.0
	}
	if model != "" {
		sess.models[model] = true
	}
	entry := TraceEntry{
		T:           time.Since(sess.startTime).Seconds(),
		Model:       model,
		In:          int(in),
		Out:         int(out),
		APITime:     apiTimeMillis / 1000.0,
		ThinkTime:   nil,
		Type:        name,
		TTFT:        ttft,
		Status:      status,
		BlockSize:   nil,
		HashIDScope: nil,
		HashIDs:     nil,
	}
	sess.requests = append(sess.requests, entry)

	t.persist()
}

// Snapshot 返回指定会话当前累积的 Trace 快照(供注入 response.body.trace)。
func (t *TraceService) Snapshot(sessionKey string) Trace {
	t.mu.Lock()
	defer t.mu.Unlock()
	if sessionKey == "" {
		sessionKey = "default"
	}
	sess, ok := t.sessions[sessionKey]
	if !ok {
		return Trace{}
	}
	models := make([]string, 0, len(sess.models))
	for m := range sess.models {
		models = append(models, m)
	}
	sort.Strings(models)
	return Trace{
		ID:          sess.traceID,
		Models:      models,
		BlockSize:   nil,
		HashIDScope: nil,
		Requests:    sess.requests,
	}
}

// persist 把各会话快照追加到 trace.jsonl(JSONL,一行一个完整快照)。
func (t *TraceService) persist() {
	f, err := os.OpenFile(t.filePath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		if t.logger != nil {
			t.logger.Printf("❌ trace open %s: %v", t.filePath, err)
		}
		return
	}
	defer f.Close()

	for _, key := range t.ordered {
		sess := t.sessions[key]
		if sess == nil {
			continue
		}
		models := make([]string, 0, len(sess.models))
		for m := range sess.models {
			models = append(models, m)
		}
		sort.Strings(models)
		snap := Trace{
			ID:          sess.traceID,
			Models:      models,
			BlockSize:   nil,
			HashIDScope: nil,
			Requests:    sess.requests,
		}
		data, err := json.Marshal(snap)
		if err != nil {
			continue
		}
		if _, err := f.Write(append(data, '\n')); err != nil {
			if t.logger != nil {
				t.logger.Printf("trace write: %v", err)
			}
			break
		}
	}
}

func newTraceID() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

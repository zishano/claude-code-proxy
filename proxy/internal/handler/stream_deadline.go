package handler

import (
	"errors"
	"fmt"
	"net/http"
	"time"
)

const streamWriteDeadlineErrorCode = "E_STREAM_WRITE_DEADLINE_UNSUPPORTED"

var errStreamWriteDeadlineUnsupported = errors.New("streaming response write deadline control unsupported")

// prepareStreamWriteDeadline removes http.Server.WriteTimeout only for a
// streaming Messages response. Non-streaming requests continue to use the
// server-wide deadline. This must run before the upstream request starts so a
// writer that cannot expose deadline control fails closed without consuming an
// upstream attempt.
func prepareStreamWriteDeadline(w http.ResponseWriter, stream bool) error {
	if !stream {
		return nil
	}
	if err := http.NewResponseController(w).SetWriteDeadline(time.Time{}); err != nil {
		return fmt.Errorf("%w: %v", errStreamWriteDeadlineUnsupported, err)
	}
	return nil
}

package validation

import (
	"fmt"
	"strings"
)

func ValidSHA256(v string) bool {
	if len(v) != 64 {
		return false
	}
	for _, r := range strings.ToLower(v) {
		if !strings.ContainsRune("0123456789abcdef", r) {
			return false
		}
	}
	return true
}

func NormalizeConsent(v string) (string, error) {
	v = strings.ToUpper(strings.TrimSpace(v))
	switch v {
	case "ACTIVE", "REVOKED", "RESTRICTED":
		return v, nil
	default:
		return "", fmt.Errorf("invalid consent state %q", v)
	}
}

func NormalizeDecision(v string) (string, error) {
	v = strings.ToUpper(strings.TrimSpace(v))
	switch v {
	case "APPROVED", "REJECTED", "REVOKED":
		return v, nil
	default:
		return "", fmt.Errorf("invalid access decision %q", v)
	}
}

package validation

import "testing"

func TestValidSHA256(t *testing.T) {
	good := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	if !ValidSHA256(good) {
		t.Fatal("expected valid sha256")
	}
	if ValidSHA256("abc") {
		t.Fatal("short hash should be invalid")
	}
	bad := "z123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	if ValidSHA256(bad) {
		t.Fatal("non-hex hash should be invalid")
	}
}

func TestNormalizeConsent(t *testing.T) {
	got, err := NormalizeConsent(" active ")
	if err != nil || got != "ACTIVE" {
		t.Fatalf("unexpected: %q %v", got, err)
	}
	if _, err := NormalizeConsent("maybe"); err == nil {
		t.Fatal("expected error")
	}
}

func TestNormalizeDecision(t *testing.T) {
	got, err := NormalizeDecision("approved")
	if err != nil || got != "APPROVED" {
		t.Fatalf("unexpected: %q %v", got, err)
	}
	if _, err := NormalizeDecision("pending"); err == nil {
		t.Fatal("expected error")
	}
}

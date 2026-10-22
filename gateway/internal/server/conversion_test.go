package server

import (
	"context"
	"errors"
	"os/exec"
	"strings"
	"testing"

	apiv1 "github.com/uw-syfi/vibesys/gateway/gen/vibesys/api/v1"
	hostv1 "github.com/uw-syfi/vibesys/gateway/gen/vibesys/host/v1"
	"github.com/uw-syfi/vibesys/gateway/internal/hostcommand"
)

func TestConversionRequiresExplicitUnknownCommandEvidence(t *testing.T) {
	cases := []struct {
		name        string
		result      hostcommand.Result
		err         error
		unsupported bool
	}{
		{"invalid choice", hostcommand.Result{ExitCode: 2, Stderr: "vibesys host: error: argument method: invalid choice: 'convert-saved-runs' (choose from 'hello')"}, &exec.ExitError{}, true},
		{"unrecognized command", hostcommand.Result{ExitCode: 2, Stderr: "vibesys: error: unrecognized arguments: convert-saved-runs"}, nil, true},
		{"empty diagnostic", hostcommand.Result{ExitCode: 2}, nil, false},
		{"generic usage", hostcommand.Result{ExitCode: 2, Stderr: "argument --project is required"}, nil, false},
		{"command only in choices", hostcommand.Result{ExitCode: 2, Stderr: "invalid choice: 'bad' (choose from 'convert-saved-runs')"}, nil, false},
		{"similar name", hostcommand.Result{ExitCode: 2, Stderr: "invalid choice: 'convert-saved-runs-other'"}, nil, false},
		{"unknown option", hostcommand.Result{ExitCode: 2, Stderr: "unrecognized arguments: --convert-saved-runs"}, nil, false},
		{"partial reply", hostcommand.Result{ExitCode: 2, Output: []byte{10}, Stderr: "invalid choice: 'convert-saved-runs'"}, &exec.ExitError{}, false},
		{"start failure", hostcommand.Result{ExitCode: 2, Stderr: "invalid choice: 'convert-saved-runs'"}, errors.New("could not start"), false},
		{"other exit", hostcommand.Result{ExitCode: 1, Stderr: "invalid choice: 'convert-saved-runs'"}, &exec.ExitError{}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			runner := hostcommand.FakeRunner{Program: func(context.Context, hostcommand.Command) (hostcommand.Result, error) { return c.result, c.err }}
			service := &host{config: Config{Root: t.TempDir(), HostRunner: runner}}
			conversion := &apiv1.MutateRequest{Checkout: "/src/checkout", Method: &apiv1.MutateRequest_ConvertSavedRuns{ConvertSavedRuns: &hostv1.ConvertSavedRunsRequest{RequestId: "convert-a", Project: "/project"}}}
			response, err := service.Mutate(context.Background(), conversion)
			want := hostv1.FailureCategory_FAILURE_CATEGORY_INTERNAL
			if c.unsupported {
				want = hostv1.FailureCategory_FAILURE_CATEGORY_UNSUPPORTED
			}
			if err != nil || response.GetFailure().GetCategory() != want {
				t.Fatalf("Mutate: %v %v, want %v", response, err, want)
			}
			if !strings.Contains(response.GetFailure().GetMessage(), c.result.Stderr) {
				t.Fatal("diagnostic was dropped")
			}
			other := &apiv1.MutateRequest{Checkout: "/src/checkout", Method: &apiv1.MutateRequest_RemoveCluster{RemoveCluster: &hostv1.RemoveClusterRequest{RequestId: "remove-a", Name: "host-a"}}}
			unrelated, err := service.Mutate(context.Background(), other)
			if err != nil || unrelated.GetFailure().GetCategory() != hostv1.FailureCategory_FAILURE_CATEGORY_INTERNAL {
				t.Fatalf("unrelated Mutate: %v %v", unrelated, err)
			}
			read := &apiv1.ReadRequest{Checkout: "/src/checkout", Method: &apiv1.ReadRequest_ListSavedRuns{ListSavedRuns: &hostv1.ListSavedRunsRequest{RequestId: "saved-a", Project: "/project"}}}
			listed, err := service.Read(context.Background(), read)
			if err != nil || listed.GetFailure().GetCategory() != hostv1.FailureCategory_FAILURE_CATEGORY_INTERNAL {
				t.Fatalf("Read: %v %v", listed, err)
			}
		})
	}
}

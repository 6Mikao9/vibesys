package server

import (
	"context"
	"errors"
	"os/exec"
	"testing"

	apiv1 "github.com/uw-syfi/vibesys/gateway/gen/vibesys/api/v1"
	hostv1 "github.com/uw-syfi/vibesys/gateway/gen/vibesys/host/v1"
	"github.com/uw-syfi/vibesys/gateway/internal/hostcommand"
)

type exitCase struct {
	name     string
	result   hostcommand.Result
	err      error
	category hostv1.FailureCategory
}

func exitCases() []exitCase {
	unsupported := hostv1.FailureCategory_FAILURE_CATEGORY_UNSUPPORTED
	internal := hostv1.FailureCategory_FAILURE_CATEGORY_INTERNAL
	return []exitCase{
		{"usage exit from an older checkout", hostcommand.Result{ExitCode: 2, Stderr: "invalid choice"}, &exec.ExitError{}, unsupported},
		{"usage exit without stderr", hostcommand.Result{ExitCode: 2}, nil, unsupported},
		{"usage status with a partial reply", hostcommand.Result{ExitCode: 2, Output: []byte{0x0a}}, &exec.ExitError{}, internal},
		{"usage status from a start failure", hostcommand.Result{ExitCode: 2}, errors.New("fork failed"), internal},
		{"crash", hostcommand.Result{ExitCode: 1, Stderr: "Traceback"}, &exec.ExitError{}, internal},
		{"signal", hostcommand.Result{ExitCode: -1}, &exec.ExitError{}, internal},
	}
}

func exitRunner(c exitCase) hostcommand.FakeRunner {
	return hostcommand.FakeRunner{Program: func(context.Context, hostcommand.Command) (hostcommand.Result, error) {
		return c.result, c.err
	}}
}

func TestMutateTypesAnUnknownCommandAsUnsupported(t *testing.T) {
	for _, c := range exitCases() {
		t.Run(c.name, func(t *testing.T) {
			service := &host{config: Config{Root: t.TempDir(), HostRunner: exitRunner(c)}}
			request := &apiv1.MutateRequest{
				Checkout: "/src/checkout",
				Method: &apiv1.MutateRequest_ConvertSavedRuns{ConvertSavedRuns: &hostv1.ConvertSavedRunsRequest{
					RequestId: "convert-a", Project: "/project",
				}},
			}
			response, err := service.Mutate(context.Background(), request)
			if err != nil || response.GetFailure().GetCategory() != c.category {
				t.Fatalf("Mutate: %v %v, want %v", response, err, c.category)
			}
		})
	}
}

func TestReadTypesAnUnknownCommandAsUnsupported(t *testing.T) {
	for _, c := range exitCases() {
		t.Run(c.name, func(t *testing.T) {
			service := &host{config: Config{Root: t.TempDir(), HostRunner: exitRunner(c)}}
			request := &apiv1.ReadRequest{
				Checkout: "/src/checkout",
				Method: &apiv1.ReadRequest_ListSavedRuns{ListSavedRuns: &hostv1.ListSavedRunsRequest{
					RequestId: "saved-a", Project: "/project",
				}},
			}
			response, err := service.Read(context.Background(), request)
			if err != nil || response.GetFailure().GetCategory() != c.category {
				t.Fatalf("Read: %v %v, want %v", response, err, c.category)
			}
		})
	}
}

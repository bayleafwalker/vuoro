package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	ec00oa "example.com/vuoro-ec00/openapi-go-v2"
)

const requestID = "9007199254741199"
const idempotencyKey = "v2-cas"

type result struct {
	Language       string `json:"language"`
	RequestID      string `json:"requestId"`
	Revision       string `json:"revision"`
	ReplayRevision string `json:"replayRevision"`
	ChangedStatus  int    `json:"changedStatus"`
}

func revise(client *ec00oa.APIClient, request *ec00oa.ReviseRequest) (*ec00oa.MutationResult, *http.Response, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return client.DefaultAPI.Revise(ctx, requestID).ReviseRequest(*request).Execute()
}

func run() (result, error) {
	baseURL := os.Getenv("EC00_BASE_URL")
	expectedRevision := os.Getenv("EC00_EXPECTED_REVISION")
	if baseURL == "" || expectedRevision == "" {
		return result{}, fmt.Errorf("EC00_BASE_URL and EC00_EXPECTED_REVISION are required")
	}

	configuration := ec00oa.NewConfiguration()
	configuration.Servers = ec00oa.ServerConfigurations{{URL: baseURL}}
	configuration.HTTPClient = &http.Client{Timeout: 5 * time.Second}
	configuration.AddDefaultHeader("Idempotency-Key", idempotencyKey)
	client := ec00oa.NewAPIClient(configuration)

	reviseRequest := ec00oa.NewReviseRequest(requestID, "versioned counter effect")
	reviseRequest.SetExpectedRevision(expectedRevision)
	first, firstResponse, err := revise(client, reviseRequest)
	if err != nil {
		return result{}, fmt.Errorf("first revise failed (status %v): %w", responseStatus(firstResponse), err)
	}
	if first == nil {
		return result{}, fmt.Errorf("first revise returned no result")
	}
	if first.RequestId != requestID {
		return result{}, fmt.Errorf("first revise returned request ID %q, expected %q", first.RequestId, requestID)
	}

	replay, replayResponse, err := revise(client, reviseRequest)
	if err != nil {
		return result{}, fmt.Errorf("same-key replay failed (status %v): %w", responseStatus(replayResponse), err)
	}
	if replay == nil {
		return result{}, fmt.Errorf("same-key replay returned no result")
	}
	if replay.RequestId != requestID {
		return result{}, fmt.Errorf("same-key replay returned request ID %q, expected %q", replay.RequestId, requestID)
	}

	changedRequest := ec00oa.NewReviseRequest(requestID, "changed versioned effect")
	changedRequest.SetExpectedRevision(expectedRevision)
	_, changedResponse, changedErr := revise(client, changedRequest)
	if changedResponse == nil || changedResponse.StatusCode != http.StatusConflict {
		return result{}, fmt.Errorf("same-key changed-body request expected HTTP 409, got %v (error: %v)",
			responseStatus(changedResponse), changedErr)
	}

	return result{
		Language:       "go",
		RequestID:      first.RequestId,
		Revision:       first.Revision,
		ReplayRevision: replay.Revision,
		ChangedStatus:  changedResponse.StatusCode,
	}, nil
}

func responseStatus(response *http.Response) any {
	if response == nil {
		return "no HTTP response"
	}
	return response.StatusCode
}

// observeUnknownValues uses the frozen generated API/decoders without filtering
// unknown fields or states. A decoder refusal is a characterization result.
func observeUnknownValues() error {
	baseURL := os.Getenv("EC00_BASE_URL")
	if baseURL == "" {
		return fmt.Errorf("EC00_BASE_URL is required")
	}
	configuration := ec00oa.NewConfiguration()
	configuration.Servers = ec00oa.ServerConfigurations{{URL: baseURL}}
	configuration.HTTPClient = &http.Client{Timeout: 5 * time.Second}
	client := ec00oa.NewAPIClient(configuration)
	observe := func(value any, err error) map[string]any {
		if err != nil {
			return map[string]any{"decoded": false, "error": fmt.Sprintf("%T", err)}
		}
		return map[string]any{"decoded": true, "value": value}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	request, _, requestErr := client.DefaultAPI.ReadRequest(ctx, requestID).Execute()
	artifact, _, artifactErr := client.DefaultAPI.ReadArtifact(ctx, requestID, "1").Execute()
	return json.NewEncoder(os.Stdout).Encode(map[string]any{
		"language": "go", "request": observe(request, requestErr), "artifact": observe(artifact, artifactErr),
	})
}

func main() {
	if mode := os.Getenv("EC00_NATIVE_MODE"); mode != "" {
		if err := nativeMode(mode); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if os.Getenv("EC00_UNKNOWN_VALUES") == "1" {
		if err := observeUnknownValues(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	output, err := run()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(output); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

// Each invocation reads coordinator-persisted restart state and constructs a
// fresh generated API client. No transport retry can invent a logical key.
func nativeMode(mode string) error {
	var state struct {
		Key              string  `json:"key"`
		RequestID        string  `json:"requestId"`
		ExpectedRevision string  `json:"expectedRevision"`
		Objective        string  `json:"objective"`
		Cursor           *string `json:"cursor"`
	}
	raw, err := os.ReadFile(os.Getenv("EC00_NATIVE_STATE"))
	if err != nil {
		return err
	}
	if err = json.Unmarshal(raw, &state); err != nil {
		return err
	}
	configuration := ec00oa.NewConfiguration()
	configuration.Servers = ec00oa.ServerConfigurations{{URL: os.Getenv("EC00_BASE_URL")}}
	configuration.HTTPClient = &http.Client{Timeout: 5 * time.Second}
	configuration.AddDefaultHeader("Idempotency-Key", state.Key)
	client := ec00oa.NewAPIClient(configuration)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	failure := os.Getenv("EC00_NATIVE_FAILURE")
	if failure == "deadline" {
		deadline := time.Now().Add(time.Second)
		if os.Getenv("EC00_NATIVE_PREDISPATCH") == "1" {
			deadline = time.Now().Add(-time.Second)
		}
		var deadlineCancel context.CancelFunc
		ctx, deadlineCancel = context.WithDeadline(ctx, deadline)
		defer deadlineCancel()
	} else if failure == "cancel" {
		if os.Getenv("EC00_NATIVE_PREDISPATCH") == "1" {
			cancel()
		} else {
			go func() { _, _ = io.ReadFull(os.Stdin, make([]byte, 1)); cancel() }()
		}
	}
	output := map[string]any{"language": "go"}
	if mode == "watch" {
		request := client.DefaultAPI.Watch(ctx).Limit(2)
		if state.Cursor != nil {
			request = request.Cursor(*state.Cursor)
		}
		page, response, callErr := request.Execute()
		output["status"], output["page"] = responseStatusNumber(response), page
		if callErr != nil {
			output["error"] = callErr.Error()
		}
	} else {
		request := ec00oa.NewReviseRequest(state.RequestID, state.Objective)
		request.SetExpectedRevision(state.ExpectedRevision)
		first, response, callErr := client.DefaultAPI.Revise(ctx, state.RequestID).ReviseRequest(*request).Execute()
		output["status"], output["first"] = responseStatusNumber(response), first
		if callErr != nil {
			output["error"] = callErr.Error()
		}
		if mode == "reconcile" {
			replay, replayResponse, replayErr := client.DefaultAPI.Revise(ctx, state.RequestID).ReviseRequest(*request).Execute()
			if replayErr != nil {
				return replayErr
			}
			request.Objective = "changed native effect"
			_, changedResponse, _ := client.DefaultAPI.Revise(ctx, state.RequestID).ReviseRequest(*request).Execute()
			output["replay"], output["replayStatus"], output["changedStatus"] = replay, responseStatusNumber(replayResponse), responseStatusNumber(changedResponse)
		}
	}
	return json.NewEncoder(os.Stdout).Encode(output)
}

func responseStatusNumber(response *http.Response) int {
	if response == nil {
		return 0
	}
	return response.StatusCode
}

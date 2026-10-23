package main

import (
	"context"
	"encoding/json"
	ec00oa "example.com/vuoro-ec00/openapi-go-v3"
	"fmt"
	"net/http"
	"os"
	"time"
)

func main() {
	baseURL := os.Getenv("EC00_BASE_URL")
	if baseURL == "" || os.Getenv("EC00_UNKNOWN_VALUES_V3") != "1" {
		fmt.Fprintln(os.Stderr, "EC00_BASE_URL and EC00_UNKNOWN_VALUES_V3=1 are required")
		os.Exit(1)
	}
	configuration := ec00oa.NewConfiguration()
	configuration.Servers = ec00oa.ServerConfigurations{{URL: baseURL}}
	configuration.HTTPClient = &http.Client{Timeout: 5 * time.Second}
	client := ec00oa.NewAPIClient(configuration)
	observe := func(value any, response *http.Response, err error) map[string]any {
		if err != nil || response == nil || response.StatusCode != 200 {
			return map[string]any{"decoded": false, "error": fmt.Sprintf("generated API/decoder refusal: %T", err)}
		}
		return map[string]any{"decoded": true, "value": value}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	request, requestResponse, requestErr := client.DefaultAPI.ReadRequest(ctx, "9007199254741199").Execute()
	artifact, artifactResponse, artifactErr := client.DefaultAPI.ReadArtifact(ctx, "9007199254741199", "1").Execute()
	if err := json.NewEncoder(os.Stdout).Encode(map[string]any{
		"language": "go", "request": observe(request, requestResponse, requestErr),
		"artifact": observe(artifact, artifactResponse, artifactErr),
	}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

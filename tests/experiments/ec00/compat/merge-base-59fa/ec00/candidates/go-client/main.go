package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	ec00oa "example.com/vuoro-ec00/openapi-go"
	pb "example.com/vuoro-ec00/ec00/generated/protobuf/go/oa/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

type result struct {
	Language string `json:"language"`
	ProtoID string `json:"protoId"`
	ProtoRevision int64 `json:"protoRevision"`
	ProtoNullNoteID string `json:"protoNullNoteId"`
	ProtoAbsentNoteID string `json:"protoAbsentNoteId"`
	ProtoAbsent string `json:"protoAbsent"`
	ProtoZero string `json:"protoZero"`
	HTTPID string `json:"httpId"`
	HTTPRevision int64 `json:"httpRevision"`
	HTTPNullNoteID string `json:"httpNullNoteId"`
	HTTPAbsentNoteID string `json:"httpAbsentNoteId"`
	HTTPAbsent int `json:"httpAbsent"`
	HTTPNull int `json:"httpNull"`
	HTTPZero int `json:"httpZero"`
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	grpcTarget := os.Getenv("EC00_GRPC_TARGET")
	baseURL := os.Getenv("EC00_BASE_URL")
	if grpcTarget == "" || baseURL == "" { return fmt.Errorf("EC00_GRPC_TARGET and EC00_BASE_URL are required") }
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	conn, err := grpc.NewClient(grpcTarget, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil { return err }
	defer conn.Close()
	client := pb.NewOwnerOperationsClient(conn)
	protoID := "9007199254741011"
	ctxKey := metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-publish")
	published, err := client.Publish(ctxKey, &pb.PublishRequest{RequestId: protoID,
		Objective: proto.String("Go native gRPC publish"), NoteValue: &pb.PublishRequest_NoteNull{NoteNull: 0}})
	if err != nil { return fmt.Errorf("protobuf publish: %w", err) }
	if published.RequestId != protoID || published.Revision != 1 { return fmt.Errorf("protobuf publish result mismatch: %+v", published) }
	protoNullID := "9007199254741031"
	_, err = client.Publish(metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-publish-null-note"),
		&pb.PublishRequest{RequestId: protoNullID, Objective: proto.String("Go native gRPC null note"),
			NoteValue: &pb.PublishRequest_NoteNull{NoteNull: 0}})
	if err != nil { return fmt.Errorf("protobuf null-note publish: %w", err) }
	protoAbsentID := "9007199254741033"
	_, err = client.Publish(metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-publish-absent-note"),
		&pb.PublishRequest{RequestId: protoAbsentID, Objective: proto.String("Go native gRPC absent note")})
	if err != nil { return fmt.Errorf("protobuf absent-note publish: %w", err) }

	absentCtx := metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-revise-absent")
	_, protoAbsentErr := client.Revise(absentCtx, &pb.ReviseRequest{RequestId: protoID, Objective: proto.String("absent")})
	if status.Code(protoAbsentErr) != codes.InvalidArgument { return fmt.Errorf("expected protobuf absent presence refusal, got %v", protoAbsentErr) }
	zeroCtx := metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-revise-zero")
	_, zeroErr := client.Revise(zeroCtx, &pb.ReviseRequest{RequestId: protoID,
		ExpectedRevision: proto.Int64(0), Objective: proto.String("zero")})
	if status.Code(zeroErr) != codes.Aborted { return fmt.Errorf("expected stale protobuf CAS refusal, got %v", zeroErr) }
	validCtx := metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-revise-valid")
	revised, err := client.Revise(validCtx, &pb.ReviseRequest{RequestId: protoID,
		ExpectedRevision: proto.Int64(1), Objective: proto.String("Go native gRPC revision"),
		NoteValue: &pb.ReviseRequest_Note{Note: ""}})
	if err != nil { return fmt.Errorf("protobuf revise: %w", err) }
	if revised.Revision != 2 { return fmt.Errorf("protobuf revision mismatch: %+v", revised) }

	configuration := ec00oa.NewConfiguration()
	configuration.Servers = ec00oa.ServerConfigurations{{URL: baseURL}}
	api := ec00oa.NewAPIClient(configuration).DefaultAPI
	httpID := "9007199254741013"
	configuration.DefaultHeader["Idempotency-Key"] = "go-openapi-publish"
	publishBody := *ec00oa.NewPublishRequest(httpID, "Go OpenAPI publish")
	publishBody.SetNoteNil()
	publishCtx := context.WithValue(ctx, ec00oa.ContextServerIndex, 0)
	publishedHTTP, _, err := api.Publish(publishCtx).PublishRequest(publishBody).Execute()
	if err != nil { return fmt.Errorf("OpenAPI publish: %w", err) }
	if publishedHTTP.RequestId != httpID || publishedHTTP.Revision != 1 { return fmt.Errorf("OpenAPI publish result mismatch: %+v", publishedHTTP) }

	callRevise := func(key string, expected *int64, nullable bool, note string) (int, *ec00oa.MutationResult, error) {
		configuration.DefaultHeader["Idempotency-Key"] = key
		body := *ec00oa.NewReviseRequest(httpID, "Go OpenAPI revision")
		if nullable {
			body.SetExpectedRevisionNil()
		} else if expected != nil {
			body.SetExpectedRevision(*expected)
		}
		body.SetNote(note)
		callCtx := context.WithValue(ctx, ec00oa.ContextServerIndex, 0)
		callResult, response, callErr := api.Revise(callCtx, httpID).ReviseRequest(body).Execute()
		if response == nil { return 0, nil, callErr }
		return response.StatusCode, callResult, callErr
	}
	absentStatus, _, absentErr := callRevise("go-openapi-revise-absent", nil, false, "ignored")
	if absentStatus != 422 || absentErr == nil { return fmt.Errorf("expected OpenAPI absent field 422, got %d, %v", absentStatus, absentErr) }
	nullStatus, _, nullErr := callRevise("go-openapi-revise-null", nil, true, "ignored")
	if nullStatus != 422 || nullErr == nil { return fmt.Errorf("expected OpenAPI null field 422, got %d, %v", nullStatus, nullErr) }
	var zero int64 = 0
	zeroStatus, _, zeroHTTPerr := callRevise("go-openapi-revise-zero", &zero, false, "ignored")
	if zeroStatus != 409 || zeroHTTPerr == nil { return fmt.Errorf("expected OpenAPI stale revision 409, got %d, %v", zeroStatus, zeroHTTPerr) }
	one := int64(1)
	validStatus, validResult, validErr := callRevise("go-openapi-revise-valid", &one, false, "")
	if validStatus != 200 || validErr != nil { return fmt.Errorf("expected OpenAPI revise 200, got %d, %v", validStatus, validErr) }
	httpNullID := "9007199254741035"
	configuration.DefaultHeader["Idempotency-Key"] = "go-openapi-publish-null-note"
	openapiNullBody := *ec00oa.NewPublishRequest(httpNullID, "Go OpenAPI null note")
	openapiNullBody.SetNoteNil()
	if _, _, err = api.Publish(publishCtx).PublishRequest(openapiNullBody).Execute(); err != nil {
		return fmt.Errorf("OpenAPI null-note publish: %w", err)
	}
	httpAbsentID := "9007199254741037"
	configuration.DefaultHeader["Idempotency-Key"] = "go-openapi-publish-absent-note"
	openapiAbsentBody := *ec00oa.NewPublishRequest(httpAbsentID, "Go OpenAPI absent note")
	if _, _, err = api.Publish(publishCtx).PublishRequest(openapiAbsentBody).Execute(); err != nil {
		return fmt.Errorf("OpenAPI absent-note publish: %w", err)
	}
	_ = json.NewEncoder(os.Stdout).Encode(result{Language: "go", ProtoID: protoID, ProtoRevision: revised.Revision,
		ProtoNullNoteID: protoNullID, ProtoAbsentNoteID: protoAbsentID,
	ProtoAbsent: status.Code(protoAbsentErr).String(), ProtoZero: status.Code(zeroErr).String(),
		HTTPID: httpID, HTTPRevision: validResult.Revision, HTTPAbsent: absentStatus,
		HTTPNullNoteID: httpNullID, HTTPAbsentNoteID: httpAbsentID,
		HTTPNull: nullStatus, HTTPZero: zeroStatus})
	return nil
}

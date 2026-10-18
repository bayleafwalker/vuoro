package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"time"

	pb "example.com/vuoro-ec00/ec00/generated/protobuf/go/oa/v1"
	ec00oa "example.com/vuoro-ec00/openapi-go"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

type result struct {
	Language          string `json:"language"`
	ProtoID           string `json:"protoId"`
	ProtoRevision     int64  `json:"protoRevision"`
	ProtoNullNoteID   string `json:"protoNullNoteId"`
	ProtoAbsentNoteID string `json:"protoAbsentNoteId"`
	ProtoAbsent       string `json:"protoAbsent"`
	ProtoZero         string `json:"protoZero"`
	HTTPID            string `json:"httpId"`
	HTTPRevision      int64  `json:"httpRevision"`
	HTTPNullNoteID    string `json:"httpNullNoteId"`
	HTTPAbsentNoteID  string `json:"httpAbsentNoteId"`
	HTTPAbsent        int    `json:"httpAbsent"`
	HTTPNull          int    `json:"httpNull"`
	HTTPZero          int    `json:"httpZero"`
}

type endpoint struct {
	HTTP string `json:"http"`
	GRPC string `json:"grpc"`
}
type artifactFixture struct {
	RequestID        string `json:"request_id"`
	Objective        string `json:"objective"`
	Digest           string `json:"artifact_digest"`
	KeyID            string `json:"key_id"`
	Original         string `json:"original_bytes"`
	Envelope         string `json:"envelope_bytes"`
	Signature        string `json:"signature"`
	Revision         string `json:"revision"`
	MaterialRevision string `json:"material_revision"`
	Sequence         string `json:"sequence"`
}

func fixtures() (map[string]endpoint, map[string]artifactFixture, error) {
	var eps map[string]endpoint
	var arts map[string]artifactFixture
	if err := json.Unmarshal([]byte(os.Getenv("EC00_ENDPOINTS")), &eps); err != nil {
		return nil, nil, err
	}
	if err := json.Unmarshal([]byte(os.Getenv("EC00_ARTIFACTS")), &arts); err != nil {
		return nil, nil, err
	}
	return eps, arts, nil
}

func runHTTPFull(eps map[string]endpoint, a artifactFixture, keyPrefix string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	clientFor := func(role, key string) (*ec00oa.DefaultAPIService, context.Context) {
		c := ec00oa.NewConfiguration()
		c.Servers = ec00oa.ServerConfigurations{{URL: eps[role].HTTP}}
		c.DefaultHeader["Idempotency-Key"] = key
		return ec00oa.NewAPIClient(c).DefaultAPI, context.WithValue(ctx, ec00oa.ContextServerIndex, 0)
	}
	bytesValue, _ := base64.StdEncoding.DecodeString(a.Original)
	envValue, _ := base64.StdEncoding.DecodeString(a.Envelope)
	sigValue, _ := base64.StdEncoding.DecodeString(a.Signature)
	pub, _ := clientFor("coordinator-a", keyPrefix+"-full-publish")
	body := ec00oa.NewPublishRequest(a.RequestID, a.Objective)
	body.Dependants = []string{"work-a", "work-b", "work-c"}
	body.SetArtifactDigest(a.Digest)
	body.SetArtifactKeyId(a.KeyID)
	body.SetArtifactBytes(base64.StdEncoding.EncodeToString(bytesValue))
	body.SetArtifactEnvelope(base64.StdEncoding.EncodeToString(envValue))
	body.SetArtifactSignature(base64.StdEncoding.EncodeToString(sigValue))
	if _, _, err := pub.Publish(ctx).PublishRequest(*body).Execute(); err == nil {
		return fmt.Errorf("%s publish fault was not observed", keyPrefix)
	}
	if _, _, err := pub.Publish(ctx).PublishRequest(*body).Execute(); err != nil {
		return fmt.Errorf("%s publish retry: %w", keyPrefix, err)
	}
	steps := []struct{ role, op string }{
		{"requester-a2", "withdraw"}, {"requester-a", "report"}, {"verifier-a", "verify"},
		{"operator-a", "decide"}, {"verifier-a", "resolve"}, {"requester-a", "ack-a"}, {"requester-a2", "ack-b"},
	}
	for i, s := range steps {
		api, c := clientFor(s.role, fmt.Sprintf("%s-full-%s", keyPrefix, s.op))
		rev := int64(i + 1)
		var err error
		switch s.op {
		case "withdraw":
			_, _, err = api.WithdrawDependent(c, a.RequestID).WithdrawRequest(*ec00oa.NewWithdrawRequest("work-c", rev)).Execute()
		case "report":
			_, _, err = api.ReportPerformed(c, a.RequestID).RevisionRequest(*ec00oa.NewRevisionRequest(rev)).Execute()
		case "verify":
			_, _, err = api.Verify(c, a.RequestID).VerifyRequest(*ec00oa.NewVerifyRequest(rev, "evidence:source:"+a.RequestID)).Execute()
		case "decide":
			_, _, err = api.DecideArtifact(c, a.RequestID).DecideArtifactRequest(*ec00oa.NewDecideArtifactRequest(rev, a.Digest)).Execute()
		case "resolve":
			_, _, err = api.Resolve(c, a.RequestID).RevisionRequest(*ec00oa.NewRevisionRequest(rev)).Execute()
		case "ack-a":
			_, _, err = api.AcknowledgeContinuation(c, a.RequestID).AcknowledgeRequest(*ec00oa.NewAcknowledgeRequest("work-a", rev)).Execute()
		case "ack-b":
			_, _, err = api.AcknowledgeContinuation(c, a.RequestID).AcknowledgeRequest(*ec00oa.NewAcknowledgeRequest("work-b", rev)).Execute()
		}
		if err != nil {
			return fmt.Errorf("%s %s role=%s expected=%d: %w", keyPrefix, s.op, s.role, rev, err)
		}
	}
	api, c := clientFor("verifier-a", keyPrefix+"-read")
	request, _, err := api.ReadRequest(c, a.RequestID).Execute()
	if err != nil {
		return err
	}
	deps, _, err := api.ReadDependants(c, a.RequestID).Execute()
	if err != nil {
		return err
	}
	artifact, _, err := api.ReadArtifact(c, a.RequestID, "1").Execute()
	if err != nil {
		return err
	}
	if request.Revision != "8" || request.MaterialRevision != "1" || request.CreatedSeq == "" || request.State != ec00oa.RESOLVED || len(deps.Items) != 3 || artifact.ArtifactDigest != a.Digest || artifact.OriginalBytes != base64.StdEncoding.EncodeToString(bytesValue) || artifact.EnvelopeBytes != base64.StdEncoding.EncodeToString(envValue) || artifact.Signature != base64.StdEncoding.EncodeToString(sigValue) {
		return fmt.Errorf("%s typed read mismatch: request=%+v deps=%+v artifact=%+v", keyPrefix, request, deps, artifact)
	}
	if sequence, e := strconv.ParseInt(request.CreatedSeq, 10, 64); e != nil || sequence <= 9007199254740991 {
		return fmt.Errorf("%s read sequence lost int64 precision: %q", keyPrefix, request.CreatedSeq)
	}
	list, _, err := api.ListRequests(c).Limit(2).Execute()
	if err != nil {
		return err
	}
	watch, _, err := api.Watch(c).Limit(2).Execute()
	if err != nil {
		return err
	}
	if len(list.Items) == 0 || len(watch.Events) == 0 {
		return fmt.Errorf("%s typed list/watch response was empty", keyPrefix)
	}
	return nil
}

func runBigRead(eps map[string]endpoint, a artifactFixture) error {
	bytesValue, _ := base64.StdEncoding.DecodeString(a.Original)
	envelope, _ := base64.StdEncoding.DecodeString(a.Envelope)
	sig, _ := base64.StdEncoding.DecodeString(a.Signature)
	apiCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	c := ec00oa.NewConfiguration()
	c.Servers = ec00oa.ServerConfigurations{{URL: eps["verifier-a"].HTTP}}
	api := ec00oa.NewAPIClient(c).DefaultAPI
	apiCtx = context.WithValue(apiCtx, ec00oa.ContextServerIndex, 0)
	r, _, e := api.ReadRequest(apiCtx, a.RequestID).Execute()
	if e != nil {
		return e
	}
	ar, _, e := api.ReadArtifact(apiCtx, a.RequestID, a.MaterialRevision).Execute()
	if e != nil {
		return e
	}
	if r.Revision != a.Revision || r.MaterialRevision != a.MaterialRevision || r.CreatedSeq != a.Sequence || ar.OriginalBytes != "" || ar.EnvelopeBytes != base64.StdEncoding.EncodeToString(envelope) || ar.Signature != base64.StdEncoding.EncodeToString(sig) {
		return fmt.Errorf("Go OpenAPI exact int64 read mismatch %+v %+v", r, ar)
	}
	return grpcCall(eps, "verifier-a", "go-big-read", func(client pb.OwnerOperationsClient, ctx context.Context) error {
		pr, x := client.ReadRequest(ctx, &pb.RequestById{RequestId: a.RequestID})
		if x != nil {
			return x
		}
		materialRevision, parseErr := strconv.ParseInt(a.MaterialRevision, 10, 64)
		if parseErr != nil {
			return parseErr
		}
		pa, x := client.ReadArtifact(ctx, &pb.ReadArtifactRequest{RequestId: a.RequestID, MaterialRevision: materialRevision})
		if x != nil {
			return x
		}
		if strconv.FormatInt(pr.Revision, 10) != a.Revision || strconv.FormatInt(pr.MaterialRevision, 10) != a.MaterialRevision || strconv.FormatInt(pr.CreatedSeq, 10) != a.Sequence || !bytes.Equal(pa.OriginalBytes, bytesValue) || !bytes.Equal(pa.EnvelopeBytes, envelope) || !bytes.Equal(pa.Signature, sig) {
			return fmt.Errorf("Go protobuf exact int64 read mismatch %+v", pr)
		}
		return nil
	})
}

func runEmptyHTTP(eps map[string]endpoint, a artifactFixture) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	c := ec00oa.NewConfiguration()
	c.Servers = ec00oa.ServerConfigurations{{URL: eps["coordinator-a"].HTTP}}
	c.DefaultHeader["Idempotency-Key"] = "go-empty-http-publish"
	api := ec00oa.NewAPIClient(c).DefaultAPI
	ctx = context.WithValue(ctx, ec00oa.ContextServerIndex, 0)
	body := ec00oa.NewPublishRequest(a.RequestID, a.Objective)
	body.SetArtifactDigest(a.Digest)
	body.SetArtifactKeyId(a.KeyID)
	body.SetArtifactBytes("")
	body.SetArtifactEnvelope(a.Envelope)
	body.SetArtifactSignature(a.Signature)
	if _, _, err := api.Publish(ctx).PublishRequest(*body).Execute(); err != nil {
		return err
	}
	read, _, err := api.ReadArtifact(ctx, a.RequestID, "1").Execute()
	if err != nil {
		return err
	}
	if read.OriginalBytes != "" || read.ArtifactDigest != a.Digest || read.EnvelopeBytes != a.Envelope || read.Signature != a.Signature {
		return fmt.Errorf("empty artifact bytes not preserved")
	}
	return nil
}

func grpcCall(eps map[string]endpoint, role, key string, fn func(pb.OwnerOperationsClient, context.Context) error) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	conn, err := grpc.NewClient(eps[role].GRPC, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return err
	}
	defer conn.Close()
	ctx = metadata.AppendToOutgoingContext(ctx, "idempotency-key", key)
	return fn(pb.NewOwnerOperationsClient(conn), ctx)
}

func runGRPCFull(eps map[string]endpoint, a artifactFixture, prefix string) error {
	original, _ := base64.StdEncoding.DecodeString(a.Original)
	envelope, _ := base64.StdEncoding.DecodeString(a.Envelope)
	signature, _ := base64.StdEncoding.DecodeString(a.Signature)
	pub := func(c pb.OwnerOperationsClient, ctx context.Context) error {
		_, e := c.Publish(ctx, &pb.PublishRequest{RequestId: a.RequestID, Objective: proto.String(a.Objective), DependentIds: []string{"work-a", "work-b", "work-c"}, ArtifactDigest: proto.String(a.Digest), ArtifactKeyId: proto.String(a.KeyID), ArtifactBytes: original, ArtifactEnvelope: envelope, ArtifactSignature: signature})
		return e
	}
	if err := grpcCall(eps, "coordinator-a", prefix+"-full-publish", pub); err == nil {
		return fmt.Errorf("%s publish fault not observed", prefix)
	}
	if err := grpcCall(eps, "coordinator-a", prefix+"-full-publish", pub); err != nil {
		return fmt.Errorf("%s retry: %w", prefix, err)
	}
	steps := []struct{ role, op string }{{"requester-a2", "withdraw"}, {"requester-a", "report"}, {"verifier-a", "verify"}, {"operator-a", "decide"}, {"verifier-a", "resolve"}, {"requester-a", "ack-a"}, {"requester-a2", "ack-b"}}
	for i, s := range steps {
		rev := int64(i + 1)
		err := grpcCall(eps, s.role, prefix+"-full-"+s.op, func(c pb.OwnerOperationsClient, ctx context.Context) error {
			switch s.op {
			case "withdraw":
				_, e := c.WithdrawDependent(ctx, &pb.WithdrawDependentRequest{RequestId: a.RequestID, DependentId: "work-c", ExpectedRevision: proto.Int64(rev)})
				return e
			case "report":
				_, e := c.ReportPerformed(ctx, &pb.ReportPerformedRequest{RequestId: a.RequestID, ExpectedRevision: proto.Int64(rev)})
				return e
			case "verify":
				_, e := c.Verify(ctx, &pb.VerifyRequest{RequestId: a.RequestID, ExpectedRevision: proto.Int64(rev), EvidenceRef: "evidence:source:" + a.RequestID})
				return e
			case "decide":
				_, e := c.DecideArtifact(ctx, &pb.DecideArtifactRequest{RequestId: a.RequestID, ExpectedRevision: proto.Int64(rev), ArtifactDigest: a.Digest})
				return e
			case "resolve":
				_, e := c.Resolve(ctx, &pb.ResolveRequest{RequestId: a.RequestID, ExpectedRevision: proto.Int64(rev)})
				return e
			case "ack-a":
				_, e := c.AcknowledgeContinuation(ctx, &pb.AcknowledgeContinuationRequest{RequestId: a.RequestID, DependentId: "work-a", ExpectedRevision: proto.Int64(rev)})
				return e
			default:
				_, e := c.AcknowledgeContinuation(ctx, &pb.AcknowledgeContinuationRequest{RequestId: a.RequestID, DependentId: "work-b", ExpectedRevision: proto.Int64(rev)})
				return e
			}
		})
		if err != nil {
			return fmt.Errorf("%s %s: %w", prefix, s.op, err)
		}
	}
	return grpcCall(eps, "verifier-a", prefix+"-read", func(c pb.OwnerOperationsClient, ctx context.Context) error {
		r, e := c.ReadRequest(ctx, &pb.RequestById{RequestId: a.RequestID})
		if e != nil {
			return e
		}
		if r.Revision != 8 || r.MaterialRevision != 1 || r.CreatedSeq == 0 || r.State != pb.RequestState_REQUEST_STATE_RESOLVED {
			return fmt.Errorf("typed protobuf request mismatch: %+v", r)
		}
		d, e := c.ReadDependants(ctx, &pb.RequestById{RequestId: a.RequestID})
		if e != nil {
			return e
		}
		if len(d.Items) != 3 {
			return fmt.Errorf("typed protobuf dependent count %d", len(d.Items))
		}
		ar, e := c.ReadArtifact(ctx, &pb.ReadArtifactRequest{RequestId: a.RequestID, MaterialRevision: 1})
		if e != nil {
			return e
		}
		if ar.ArtifactDigest != a.Digest || !bytes.Equal(ar.OriginalBytes, original) || !bytes.Equal(ar.EnvelopeBytes, envelope) || !bytes.Equal(ar.Signature, signature) {
			return fmt.Errorf("typed protobuf artifact mismatch")
		}
		list, e := c.ListRequests(ctx, &pb.PageRequest{Limit: 2})
		if e != nil {
			return e
		}
		watch, e := c.Watch(ctx, &pb.PageRequest{Limit: 2})
		if e != nil {
			return e
		}
		if len(list.Items) == 0 || len(watch.Events) == 0 {
			return fmt.Errorf("typed protobuf list/watch response was empty")
		}
		return nil
	})
}

func runEmptyGRPC(eps map[string]endpoint, a artifactFixture) error {
	original, _ := base64.StdEncoding.DecodeString(a.Original)
	envelope, _ := base64.StdEncoding.DecodeString(a.Envelope)
	signature, _ := base64.StdEncoding.DecodeString(a.Signature)
	return grpcCall(eps, "coordinator-a", "go-grpc-empty-publish", func(c pb.OwnerOperationsClient, ctx context.Context) error {
		_, e := c.Publish(ctx, &pb.PublishRequest{RequestId: a.RequestID, Objective: proto.String(a.Objective), ArtifactDigest: proto.String(a.Digest), ArtifactKeyId: proto.String(a.KeyID), ArtifactBytes: original, ArtifactEnvelope: envelope, ArtifactSignature: signature})
		if e != nil {
			return e
		}
		r, e := c.ReadArtifact(ctx, &pb.ReadArtifactRequest{RequestId: a.RequestID, MaterialRevision: 1})
		if e != nil {
			return e
		}
		if !bytes.Equal(r.OriginalBytes, original) || !bytes.Equal(r.EnvelopeBytes, envelope) || !bytes.Equal(r.Signature, signature) || r.ArtifactDigest != a.Digest {
			return fmt.Errorf("empty protobuf artifact was not preserved")
		}
		return nil
	})
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
	if grpcTarget == "" || baseURL == "" {
		return fmt.Errorf("EC00_GRPC_TARGET and EC00_BASE_URL are required")
	}
	eps, artifacts, err := fixtures()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	conn, err := grpc.NewClient(grpcTarget, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return err
	}
	defer conn.Close()
	client := pb.NewOwnerOperationsClient(conn)
	protoID := "9007199254741011"
	ctxKey := metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-publish")
	published, err := client.Publish(ctxKey, &pb.PublishRequest{RequestId: protoID,
		Objective: proto.String("Go native gRPC publish"), NoteValue: &pb.PublishRequest_NoteNull{NoteNull: 0}})
	if err != nil {
		return fmt.Errorf("protobuf publish: %w", err)
	}
	if published.RequestId != protoID || published.Revision != 1 {
		return fmt.Errorf("protobuf publish result mismatch: %+v", published)
	}
	protoNullID := "9007199254741031"
	_, err = client.Publish(metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-publish-null-note"),
		&pb.PublishRequest{RequestId: protoNullID, Objective: proto.String("Go native gRPC null note"),
			NoteValue: &pb.PublishRequest_NoteNull{NoteNull: 0}})
	if err != nil {
		return fmt.Errorf("protobuf null-note publish: %w", err)
	}
	protoAbsentID := "9007199254741033"
	_, err = client.Publish(metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-publish-absent-note"),
		&pb.PublishRequest{RequestId: protoAbsentID, Objective: proto.String("Go native gRPC absent note")})
	if err != nil {
		return fmt.Errorf("protobuf absent-note publish: %w", err)
	}

	absentCtx := metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-revise-absent")
	_, protoAbsentErr := client.Revise(absentCtx, &pb.ReviseRequest{RequestId: protoID, Objective: proto.String("absent")})
	if status.Code(protoAbsentErr) != codes.InvalidArgument {
		return fmt.Errorf("expected protobuf absent presence refusal, got %v", protoAbsentErr)
	}
	zeroCtx := metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-revise-zero")
	_, zeroErr := client.Revise(zeroCtx, &pb.ReviseRequest{RequestId: protoID,
		ExpectedRevision: proto.Int64(0), Objective: proto.String("zero")})
	if status.Code(zeroErr) != codes.Aborted {
		return fmt.Errorf("expected stale protobuf CAS refusal, got %v", zeroErr)
	}
	validCtx := metadata.AppendToOutgoingContext(ctx, "idempotency-key", "go-grpc-revise-valid")
	revised, err := client.Revise(validCtx, &pb.ReviseRequest{RequestId: protoID,
		ExpectedRevision: proto.Int64(1), Objective: proto.String("Go native gRPC revision"),
		NoteValue: &pb.ReviseRequest_Note{Note: ""}})
	if err != nil {
		return fmt.Errorf("protobuf revise: %w", err)
	}
	if revised.Revision != 2 {
		return fmt.Errorf("protobuf revision mismatch: %+v", revised)
	}

	configuration := ec00oa.NewConfiguration()
	configuration.Servers = ec00oa.ServerConfigurations{{URL: baseURL}}
	api := ec00oa.NewAPIClient(configuration).DefaultAPI
	httpID := "9007199254741013"
	configuration.DefaultHeader["Idempotency-Key"] = "go-openapi-publish"
	publishBody := *ec00oa.NewPublishRequest(httpID, "Go OpenAPI publish")
	publishBody.SetNoteNil()
	publishCtx := context.WithValue(ctx, ec00oa.ContextServerIndex, 0)
	publishedHTTP, _, err := api.Publish(publishCtx).PublishRequest(publishBody).Execute()
	if err != nil {
		return fmt.Errorf("OpenAPI publish: %w", err)
	}
	if publishedHTTP.RequestId != httpID || publishedHTTP.Revision != 1 {
		return fmt.Errorf("OpenAPI publish result mismatch: %+v", publishedHTTP)
	}

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
		if response == nil {
			return 0, nil, callErr
		}
		return response.StatusCode, callResult, callErr
	}
	absentStatus, _, absentErr := callRevise("go-openapi-revise-absent", nil, false, "ignored")
	if absentStatus != 422 || absentErr == nil {
		return fmt.Errorf("expected OpenAPI absent field 422, got %d, %v", absentStatus, absentErr)
	}
	nullStatus, _, nullErr := callRevise("go-openapi-revise-null", nil, true, "ignored")
	if nullStatus != 422 || nullErr == nil {
		return fmt.Errorf("expected OpenAPI null field 422, got %d, %v", nullStatus, nullErr)
	}
	var zero int64 = 0
	zeroStatus, _, zeroHTTPerr := callRevise("go-openapi-revise-zero", &zero, false, "ignored")
	if zeroStatus != 409 || zeroHTTPerr == nil {
		return fmt.Errorf("expected OpenAPI stale revision 409, got %d, %v", zeroStatus, zeroHTTPerr)
	}
	one := int64(1)
	validStatus, validResult, validErr := callRevise("go-openapi-revise-valid", &one, false, "")
	if validStatus != 200 || validErr != nil {
		return fmt.Errorf("expected OpenAPI revise 200, got %d, %v", validStatus, validErr)
	}
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
	if err = runHTTPFull(eps, artifacts["go_http"], "go-http"); err != nil {
		return err
	}
	if err = runEmptyHTTP(eps, artifacts["go_empty_http"]); err != nil {
		return err
	}
	if err = runGRPCFull(eps, artifacts["go_proto"], "go-grpc"); err != nil {
		return err
	}
	if err = runEmptyGRPC(eps, artifacts["go_empty_proto"]); err != nil {
		return err
	}
	if err = runBigRead(eps, artifacts["big_read"]); err != nil {
		return err
	}
	_ = json.NewEncoder(os.Stdout).Encode(result{Language: "go", ProtoID: protoID, ProtoRevision: revised.Revision,
		ProtoNullNoteID: protoNullID, ProtoAbsentNoteID: protoAbsentID,
		ProtoAbsent: status.Code(protoAbsentErr).String(), ProtoZero: status.Code(zeroErr).String(),
		HTTPID: httpID, HTTPRevision: validResult.Revision, HTTPAbsent: absentStatus,
		HTTPNullNoteID: httpNullID, HTTPAbsentNoteID: httpAbsentID,
		HTTPNull: nullStatus, HTTPZero: zeroStatus})
	return nil
}

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"time"

	pb "example.com/vuoro-ec00/ec00/generated/protobuf/go/oa/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

type result struct {
	Language   string   `json:"language"`
	RequestIDs []string `json:"request_ids"`
	Revisions  []int64  `json:"revisions"`
}

func callContext(parent context.Context, key string) context.Context {
	return metadata.AppendToOutgoingContext(parent, "idempotency-key", key)
}

func requireCode(call func() error, expected codes.Code) error {
	err := call()
	if status.Code(err) != expected {
		return fmt.Errorf("expected status %s, got %s: %v", expected, status.Code(err), err)
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	target := os.Getenv("EC00_GRPC_TARGET")
	language := os.Getenv("EC00_COMPAT_LANGUAGE")
	base, err := strconv.ParseInt(os.Getenv("EC00_COMPAT_ID_BASE"), 10, 64)
	if target == "" || err != nil || (language != "go" && language != "rust") {
		return fmt.Errorf("EC00_GRPC_TARGET, EC00_COMPAT_ID_BASE and supported EC00_COMPAT_LANGUAGE are required")
	}
	displayLanguage := "Go"
	if language == "rust" {
		displayLanguage = "Rust"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	conn, err := grpc.NewClient(target, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return err
	}
	defer conn.Close()
	client := pb.NewOwnerOperationsClient(conn)
	id := strconv.FormatInt(base, 10)
	publish := &pb.PublishRequest{RequestId: id, Objective: proto.String(displayLanguage + " current common publish")}
	publishKey := "current-common-publish-" + id
	first, err := client.Publish(callContext(ctx, publishKey), publish)
	if err != nil {
		return fmt.Errorf("publish: %w", err)
	}
	replayed, err := client.Publish(callContext(ctx, publishKey), publish)
	if err != nil || first.Revision != 1 || replayed.Revision != 1 {
		return fmt.Errorf("publish replay mismatch first=%v replay=%v err=%v", first, replayed, err)
	}
	changedKeyBody := &pb.PublishRequest{RequestId: id, Objective: proto.String("different body")}
	if err := requireCode(func() error { _, e := client.Publish(callContext(ctx, publishKey), changedKeyBody); return e }, codes.Aborted); err != nil {
		return fmt.Errorf("same-key changed-body refusal: %w", err)
	}

	missing := &pb.ReviseRequest{RequestId: id, Objective: proto.String("missing CAS")}
	if err := requireCode(func() error { _, e := client.Revise(callContext(ctx, "missing-"+id), missing); return e }, codes.InvalidArgument); err != nil {
		return fmt.Errorf("missing revision refusal: %w", err)
	}
	zero := &pb.ReviseRequest{RequestId: id, ExpectedRevision: proto.Int64(0), Objective: proto.String("zero CAS")}
	if err := requireCode(func() error { _, e := client.Revise(callContext(ctx, "zero-"+id), zero); return e }, codes.Aborted); err != nil {
		return fmt.Errorf("zero revision refusal: %w", err)
	}
	revise := &pb.ReviseRequest{RequestId: id, ExpectedRevision: proto.Int64(1),
		Objective: proto.String(displayLanguage + " current common revise"), NoteValue: &pb.ReviseRequest_Note{Note: ""}}
	reviseKey := "current-common-revise-" + id
	updated, err := client.Revise(callContext(ctx, reviseKey), revise)
	if err != nil {
		return fmt.Errorf("revise: %w", err)
	}
	reviseReplay, err := client.Revise(callContext(ctx, reviseKey), revise)
	if err != nil || updated.Revision != 2 || reviseReplay.Revision != 2 {
		return fmt.Errorf("revise replay mismatch updated=%v replay=%v err=%v", updated, reviseReplay, err)
	}
	stale := &pb.ReviseRequest{RequestId: id, ExpectedRevision: proto.Int64(1), Objective: proto.String("stale")}
	if err := requireCode(func() error { _, e := client.Revise(callContext(ctx, "stale-"+id), stale); return e }, codes.Aborted); err != nil {
		return fmt.Errorf("stale revision refusal: %w", err)
	}

	ids := []string{id}
	for offset, kind := range []string{"absent", "empty", "null"} {
		requestID := strconv.FormatInt(base+int64((offset+1)*2), 10)
		message := &pb.PublishRequest{RequestId: requestID, Objective: proto.String("note presence " + kind)}
		switch kind {
		case "empty":
			message.NoteValue = &pb.PublishRequest_Note{Note: ""}
		case "null":
			message.NoteValue = &pb.PublishRequest_NoteNull{NoteNull: 0}
		}
		created, e := client.Publish(callContext(ctx, "presence-"+requestID), message)
		if e != nil || created.Revision != 1 {
			return fmt.Errorf("publish %s note state: result=%v err=%v", kind, created, e)
		}
		ids = append(ids, requestID)
	}
	_ = json.NewEncoder(os.Stdout).Encode(result{Language: language,
		RequestIDs: ids, Revisions: []int64{first.Revision, updated.Revision}})
	return nil
}

package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"time"

	pb "example.com/vuoro-ec00/ec00/generated/protobuf/go/oa/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

type resultRow struct {
	Route       string  `json:"route"`
	Case        string  `json:"case"`
	RequestHex  string  `json:"requestHex"`
	ResponseHex *string `json:"responseHex"`
	Status      string  `json:"status"`
}

type inputCase struct {
	name   string
	cursor *string
	limit  *int32
}

func ptrString(value string) *string { return &value }
func ptrInt32(value int32) *int32    { return &value }

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: go run ./go-read-presence <grpc-target>")
		os.Exit(2)
	}

	conn, err := grpc.NewClient(os.Args[1], grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer conn.Close()
	client := pb.NewOwnerOperationsClient(conn)

	cases := []inputCase{
		{name: "omitted"},
		{name: "zero", limit: ptrInt32(0)},
		{name: "empty", cursor: ptrString("")},
		{name: "one", limit: ptrInt32(1)},
		{name: "negative", limit: ptrInt32(-1)},
		{name: "oversized", limit: ptrInt32(3)},
	}
	rows := make([]resultRow, 0, len(cases)*2)
	for _, route := range []string{"list-requests", "watch"} {
		for _, testCase := range cases {
			request := &pb.PageRequestV2{Cursor: testCase.cursor, Limit: testCase.limit}
			requestBytes, marshalErr := proto.Marshal(request)
			if marshalErr != nil {
				fmt.Fprintln(os.Stderr, marshalErr)
				os.Exit(1)
			}
			callCtx, callCancel := context.WithTimeout(context.Background(), 5*time.Second)
			var responseBytes []byte
			var callErr error
			if route == "list-requests" {
				response, err := client.ListRequestsV2(callCtx, request)
				callErr = err
				if err == nil {
					responseBytes, callErr = proto.Marshal(response)
				}
			} else {
				response, err := client.WatchV2(callCtx, request)
				callErr = err
				if err == nil {
					responseBytes, callErr = proto.Marshal(response)
				}
			}
			callCancel()

			row := resultRow{
				Route:      route,
				Case:       testCase.name,
				RequestHex: hex.EncodeToString(requestBytes),
				Status:     codes.OK.String(),
			}
			if callErr != nil {
				row.Status = status.Code(callErr).String()
			} else {
				encoded := hex.EncodeToString(responseBytes)
				row.ResponseHex = &encoded
			}
			rows = append(rows, row)
		}
	}
	if err := json.NewEncoder(os.Stdout).Encode(rows); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

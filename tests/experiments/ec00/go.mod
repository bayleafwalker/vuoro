module example.com/vuoro-ec00/ec00

go 1.26.0

toolchain go1.26.8

require (
	example.com/vuoro-ec00/openapi-go-v3 v0.0.0
	example.com/vuoro-ec00/openapi-go-v2 v0.0.0
	example.com/vuoro-ec00/openapi-go v0.0.0
	google.golang.org/grpc v1.84.0
	google.golang.org/protobuf v1.36.12
)

require (
	golang.org/x/net v0.57.0 // indirect
	golang.org/x/sys v0.47.0 // indirect
	golang.org/x/text v0.40.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260706201446-f0a921348800 // indirect
)

replace example.com/vuoro-ec00/openapi-go => ./generated/typespec/clients/go

replace example.com/vuoro-ec00/openapi-go-v2 => ./generated/typespec-v2/clients/go

replace example.com/vuoro-ec00/openapi-go-v3 => ./generated/typespec-v3/clients/go

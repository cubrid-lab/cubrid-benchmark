module github.com/cubrid-lab/cubrid-benchmark/go

go 1.22

// github.com/cubrid-labs/cubrid-go stays on the old path until cubrid-go publishes a
// release under github.com/cubrid-lab/cubrid-go; then switch this require and the imports.
require (
	github.com/cubrid-labs/cubrid-go v0.2.1
	github.com/go-sql-driver/mysql v1.8.1
)

require filippo.io/edwards25519 v1.1.0 // indirect

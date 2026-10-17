#ifndef RDS_BUS_H
#define RDS_BUS_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct RdsBus RdsBus;
typedef struct RdsPacket RdsPacket;
typedef struct RdsJob RdsJob;
typedef struct RdsGraph RdsGraph;
enum { RDS_OK = 0, RDS_INVALID = -1, RDS_FULL = -2,
       RDS_EMPTY = -3, RDS_POISONED = -4 };
const char *rds_bus_version(void);
uint32_t rds_bus_abi_version(void); /* ABI version 2 */
RdsBus *rds_bus_init(size_t capacity);
RdsBus *rds_bus_init_bounded(size_t capacity, size_t max_bytes);
void rds_bus_destroy(RdsBus *bus);
RdsPacket *rds_bus_packet_new(uint32_t kind, size_t len);
uint8_t *rds_bus_packet_data(RdsPacket *packet);
size_t rds_bus_packet_len(const RdsPacket *packet);
uint32_t rds_bus_packet_kind(const RdsPacket *packet);
void rds_bus_packet_free(RdsPacket *packet);
/* Ownership transfers only on RDS_OK. Invalidate all prior data views then. */
int32_t rds_bus_post_packet(const RdsBus *bus, RdsPacket *packet);
int32_t rds_bus_post_event(const RdsBus *bus, uint32_t kind,
                           const uint8_t *data, size_t len);
/* On success, caller owns *out and must free it. */
int32_t rds_bus_poll_event(const RdsBus *bus, RdsPacket **out);
RdsJob *rds_bus_job_create(void);
int32_t rds_bus_job_bind(const RdsJob *job, void *process_handle);
/* Closes the job handle; bound processes terminate on last handle close. */
void rds_bus_job_destroy(RdsJob *job);
enum { RDS_NODE_CLAIM = 1, RDS_NODE_PREMISE = 2, RDS_NODE_OBSTRUCTION = 3,
       RDS_NODE_ANALOGY = 4, RDS_NODE_GOAL = 5 };
enum { RDS_TRUTH_UNKNOWN = 0, RDS_TRUTH_TRUE = 1, RDS_TRUTH_FALSE = 2 };
enum { RDS_EDGE_AND_DEDUCTION = 1, RDS_EDGE_OR_ALTERNATIVE = 2,
       RDS_EDGE_OBSTRUCTS = 3, RDS_EDGE_ISOMORPHISM = 4 };
/* Graph calls require caller serialization. Bounded creation returns NULL on
   invalid limits; add_node/add_edge return UINT32_MAX on rejection. */
RdsGraph *rds_graph_create(void);
RdsGraph *rds_graph_create_bounded(size_t nodes, size_t edges, size_t bytes);
void rds_graph_destroy(RdsGraph *graph);
/* fact_hash points to exactly 32 readable bytes. */
uint32_t rds_graph_add_node(RdsGraph *graph, uint8_t kind,
                            const uint8_t *fact_hash, uint8_t truth);
int32_t rds_graph_set_truth(RdsGraph *graph, uint32_t node_id, uint8_t truth);
uint32_t rds_graph_add_edge(RdsGraph *graph, uint8_t kind,
                            const uint32_t *sources, size_t source_count,
                            uint32_t target);
/* Query result: 0 success, -1 invalid, -2 insufficient capacity (no partial
   write). len is required; NULL buf queries size. cap counts uint32_t entries.
   On valid queries *len receives the required size, including on -2.
   Input/output storage must be valid, appropriately sized and non-overlapping. */
int32_t rds_graph_backward_dependencies(const RdsGraph *graph, uint32_t target,
                                        uint32_t *buf, size_t cap, size_t *len);
int32_t rds_graph_unresolved_frontier(const RdsGraph *graph, uint32_t target,
                                      uint32_t *buf, size_t cap, size_t *len);
int32_t rds_graph_forward_closure(const RdsGraph *graph, const uint32_t *seeds,
                                  size_t count, uint32_t *buf, size_t cap,
                                  size_t *len);
int32_t rds_graph_clear(RdsGraph *graph);
#ifdef __cplusplus
}
#endif
#endif

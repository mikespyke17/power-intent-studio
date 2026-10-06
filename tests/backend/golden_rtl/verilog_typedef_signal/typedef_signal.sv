package word_pkg;
  typedef logic [7:0] byte_t;
endpackage

module leaf (
  input  word_pkg::byte_t in_byte,
  output word_pkg::byte_t out_byte
);
  word_pkg::byte_t stage;
  assign stage    = in_byte;
  assign out_byte = stage;
endmodule

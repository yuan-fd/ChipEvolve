module tb;
  reg clk = 0, rst_n = 0, start = 0;
  reg [7:0] data = 8'ha5;
  wire tx, busy;
  reg [9:0] seen;
  integer i;
  uart_tx dut(clk, rst_n, start, data, tx, busy);
  always #5 clk = ~clk;
  initial begin
    repeat (2) @(posedge clk);
    rst_n = 1;
    @(negedge clk); start = 1;
    @(posedge clk);
    @(negedge clk); start = 0; seen[0] = tx;
    for (i = 1; i < 10; i = i + 1) begin
      @(negedge clk); seen[i] = tx;
    end
    if (seen !== 10'b1101001010) $fatal(1, "uart framing");
    if (busy !== 0) $fatal(1, "uart busy did not clear");
    $display("TB_SUMMARY total=2 errors=0");
    $display("PASS");
    $finish;
  end
endmodule

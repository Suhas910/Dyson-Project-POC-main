import { useState, useMemo } from "react";
import {
  Box,
  Chip,
  IconButton,
  InputAdornment,
  Tab,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  TablePagination,
  TextField,
  Typography,
  Tooltip,
  Paper,
  Button,
  MenuItem,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import DownloadIcon from "@mui/icons-material/Download";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import ArticleOutlinedIcon from "@mui/icons-material/ArticleOutlined";
import PictureAsPdfIcon from "@mui/icons-material/PictureAsPdf";

import { statusToken, SEVERITY_TOKENS } from "../theme";
import FindingDetail from "./FindingDetail";

// Most severe first, so the default sort surfaces what needs attention.
const STATUS_ORDER = {
  "NON-COMPLIANT": 0,
  NEEDS_REVIEW: 1,
  COMPLIANT: 2,
  ERROR: 3,
  NOT_EVALUATED: 4,
};

const SEVERITY_ORDER = { critical: 0, major: 1, minor: 2 };

// Cells that open the explanation. The affordance has to be visible without
// being loud -- these sit in a dense table where a button in every row would
// drown the data.
const clickable = {
  cursor: "pointer",
  "&:hover": { backgroundColor: "brand.violetTint" },
};

const COLUMNS = [
  { id: "process_family", label: "Rule Set", sortable: true },
  { id: "rule_id", label: "Rule ID", sortable: true },
  { id: "rule_name", label: "Rule Name", sortable: true },
  { id: "severity", label: "Severity", sortable: true },
  { id: "status", label: "Status", sortable: true },
  { id: "feature_label", label: "Feature", sortable: true },
  { id: "measurement_point", label: "Location (XYZ)", sortable: false },
  { id: "measured", label: "Measured", sortable: true },
  { id: "reason", label: "Notes", sortable: false },
  { id: "agent_commentary", label: "AI Commentary", sortable: false },
];

/**
 * Status and severity both render as tinted chips: pale fill, saturated text,
 * matching border. At table density a column of solid saturated blocks becomes
 * noise, while the tinted form keeps the colour readable as information.
 */
function TokenChip({ token, label }) {
  return (
    <Chip
      label={label}
      size="small"
      sx={{
        backgroundColor: token.tint,
        // `text`, not `main`: the saturated Pantone greens and greys fall just
        // below the contrast needed for label-size text on their own tints.
        color: token.text || token.main,
        border: `1px solid ${token.border}`,
        fontWeight: 600,
      }}
    />
  );
}

function getStatusChip(status) {
  const token = statusToken(status);
  return <TokenChip token={token} label={token.label} />;
}

function getSeverityChip(severity) {
  if (!severity) return null;
  const token = SEVERITY_TOKENS[severity] || SEVERITY_TOKENS.minor;
  return <TokenChip token={token} label={severity} />;
}

function exportToCSV(findings) {
  const headers = [
    "Rule ID",
    "Rule Name",
    "Guideline",
    "Severity",
    "Status",
    "Feature",
    "Location",
    "Location (XYZ)",
    "Measured",
    "Notes",
    "AI Commentary",
    "AI Confidence",
  ];
  const rows = findings.map((f) => [
    f.rule_id,
    f.rule_name,
    f.guideline_ref,
    f.severity || "N/A",
    f.status,
    f.feature_label || "",
    f.location,
    f.measurement_point
      ? `(${f.measurement_point[0]}, ${f.measurement_point[1]}, ${f.measurement_point[2]})`
      : "N/A",
    f.measured || "N/A",
    f.reason || "",
    f.agent_commentary || "N/A",
    f.agent_confidence != null ? f.agent_confidence.toFixed(2) : "N/A",
  ]);

  const csvContent = [headers, ...rows]
    .map((row) =>
      row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","),
    )
    .join("\n");

  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "dfm_analysis_report.csv";
  link.click();
  URL.revokeObjectURL(url);
}

function FindingsTable({ findings, versionId, rules = [] }) {
  const [statusFilter, setStatusFilter] = useState("ALL");
  // When a part is checked against several process families at once, the whole
  // table at once is rarely what anybody wants to read. Filtering by rule set
  // turns one long list back into the per-process sections it really is.
  const [familyFilter, setFamilyFilter] = useState("ALL");
  // The finding whose explanation is open. The table says a rule failed; this
  // says by how much, against what limit, and why that matters.
  const [detail, setDetail] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  // Default to the most severe findings first rather than rule order, so the
  // first screen shows what actually needs attention.
  const [order, setOrder] = useState("asc");
  const [orderBy, setOrderBy] = useState("status");
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(15);

  const filteredFindings = useMemo(() => {
    let data = [...findings];

    if (statusFilter !== "ALL") {
      data = data.filter((f) => f.status === statusFilter);
    }

    if (familyFilter !== "ALL") {
      data = data.filter((f) => f.process_family === familyFilter);
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      data = data.filter(
        (f) =>
          (f.rule_id && f.rule_id.toLowerCase().includes(q)) ||
          (f.rule_name && f.rule_name.toLowerCase().includes(q)) ||
          (f.location && f.location.toLowerCase().includes(q)) ||
          (f.feature_label && f.feature_label.toLowerCase().includes(q)) ||
          (f.guideline_ref && f.guideline_ref.toLowerCase().includes(q)) ||
          (f.measured && f.measured.toLowerCase().includes(q)) ||
          (f.agent_commentary && f.agent_commentary.toLowerCase().includes(q)),
      );
    }

    data.sort((a, b) => {
      let aVal = a[orderBy];
      let bVal = b[orderBy];

      if (orderBy === "status") {
        aVal = STATUS_ORDER[aVal] ?? 99;
        bVal = STATUS_ORDER[bVal] ?? 99;
      } else if (orderBy === "severity") {
        aVal = SEVERITY_ORDER[aVal] ?? 99;
        bVal = SEVERITY_ORDER[bVal] ?? 99;
      } else if (orderBy === "agent_confidence") {
        aVal = aVal ?? -1;
        bVal = bVal ?? -1;
      } else {
        aVal = String(aVal ?? "").toLowerCase();
        bVal = String(bVal ?? "").toLowerCase();
      }

      if (aVal < bVal) return order === "asc" ? -1 : 1;
      if (aVal > bVal) return order === "asc" ? 1 : -1;
      return 0;
    });

    return data;
  }, [findings, statusFilter, familyFilter, searchQuery, order, orderBy]);

  const handleSort = (columnId) => {
    const isAsc = orderBy === columnId && order === "asc";
    setOrder(isAsc ? "desc" : "asc");
    setOrderBy(columnId);
  };

  const handleChangePage = (event, newPage) => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event) => {
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  const paginatedFindings = filteredFindings.slice(
    page * rowsPerPage,
    page * rowsPerPage + rowsPerPage,
  );

  const rulesById = useMemo(
    () => new Map(rules.map((rule) => [rule.rule_id, rule])),
    [rules],
  );

  const families = useMemo(() => {
    const counts = new Map();
    findings.forEach((f) => {
      if (f.process_family) {
        counts.set(f.process_family, (counts.get(f.process_family) || 0) + 1);
      }
    });
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [findings]);

  const statusCounts = useMemo(() => {
    const scoped =
      familyFilter === "ALL"
        ? findings
        : findings.filter((f) => f.process_family === familyFilter);
    const counts = {
      ALL: scoped.length,
      COMPLIANT: 0,
      "NON-COMPLIANT": 0,
      NEEDS_REVIEW: 0,
      NOT_EVALUATED: 0,
      ERROR: 0,
    };
    scoped.forEach((f) => {
      if (counts[f.status] !== undefined) counts[f.status]++;
    });
    return counts;
  }, [findings, familyFilter]);

  return (
    <Paper sx={{ width: "100%" }}>
      <Box
        sx={{
          p: 2,
          display: "flex",
          flexDirection: { xs: "column", sm: "row" },
          alignItems: { sm: "center" },
          gap: 2,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Typography variant="h6" sx={{ flexGrow: 1 }}>
          DFM Findings
        </Typography>
        {/* Only worth showing when there is more than one rule set to choose
            between; on a single-family run it would be a control with one
            option. */}
        {families.length > 1 && (
          <TextField
            select
            size="small"
            label="Rule set"
            value={familyFilter}
            onChange={(e) => {
              setFamilyFilter(e.target.value);
              setPage(0);
            }}
            sx={{ minWidth: 220 }}
          >
            <MenuItem value="ALL">All rule sets ({findings.length})</MenuItem>
            {families.map(([family, count]) => (
              <MenuItem key={family} value={family}>
                {family} ({count})
              </MenuItem>
            ))}
          </TextField>
        )}
        <TextField
          size="small"
          placeholder="Search findings..."
          value={searchQuery}
          onChange={(e) => {
            setSearchQuery(e.target.value);
            setPage(0);
          }}
          sx={{ minWidth: 240 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" color="action" />
              </InputAdornment>
            ),
          }}
        />
        {/* The report is rendered from the stored analysis, so these open
            instantly and cost nothing to regenerate. */}
        {versionId != null && (
          <>
            <Tooltip title="Open the full report">
              <Button
                size="small"
                variant="outlined"
                startIcon={<ArticleOutlinedIcon />}
                onClick={() =>
                  window.open(
                    `/api/report/${versionId}.html`,
                    "_blank",
                    "noopener",
                  )
                }
                sx={{ mr: 1, whiteSpace: "nowrap" }}
              >
                Report
              </Button>
            </Tooltip>
            <Tooltip title="Download the report as PDF">
              <Button
                size="small"
                variant="contained"
                startIcon={<PictureAsPdfIcon />}
                onClick={() =>
                  window.open(
                    `/api/report/${versionId}.pdf`,
                    "_blank",
                    "noopener",
                  )
                }
                sx={{ mr: 1, whiteSpace: "nowrap" }}
              >
                PDF
              </Button>
            </Tooltip>
          </>
        )}
        <Tooltip title="Export findings as CSV">
          <IconButton
            onClick={() => exportToCSV(filteredFindings)}
            size="small"
            color="primary"
          >
            <DownloadIcon />
          </IconButton>
        </Tooltip>
      </Box>

      <Tabs
        value={statusFilter}
        onChange={(e, v) => {
          setStatusFilter(v);
          setPage(0);
        }}
        sx={{
          px: 2,
          borderBottom: 1,
          borderColor: "divider",
          minHeight: 40,
        }}
      >
        <Tab
          label={`All (${statusCounts.ALL})`}
          value="ALL"
          sx={{ minHeight: 40, textTransform: "none", fontWeight: 600 }}
        />
        {[
          ["NON-COMPLIANT", statusCounts["NON-COMPLIANT"]],
          ["NEEDS_REVIEW", statusCounts.NEEDS_REVIEW],
          ["COMPLIANT", statusCounts.COMPLIANT],
          ["NOT_EVALUATED", statusCounts.NOT_EVALUATED],
        ].map(([status, count]) => (
          <Tab
            key={status}
            label={`${statusToken(status).label} (${count})`}
            value={status}
            sx={{
              minHeight: 40,
              color: statusToken(status).text,
              "&.Mui-selected": { color: statusToken(status).text },
            }}
          />
        ))}
      </Tabs>

      {filteredFindings.length === 0 ? (
        <Box
          sx={{
            p: 6,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 1,
          }}
        >
          <InfoOutlinedIcon sx={{ fontSize: 48, color: "text.secondary" }} />
          <Typography variant="h6" color="text.secondary">
            No findings match your criteria
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {findings.length === 0
              ? "Upload a STEP file to begin analysis."
              : "Try adjusting your search or filter."}
          </Typography>
        </Box>
      ) : (
        <>
          <TableContainer sx={{ maxHeight: "calc(100vh - 420px)" }}>
            <Table stickyHeader size="small">
              <TableHead>
                <TableRow>
                  {COLUMNS.map((col) => (
                    <TableCell key={col.id} sx={{ whiteSpace: "nowrap" }}>
                      {col.sortable ? (
                        <TableSortLabel
                          active={orderBy === col.id}
                          direction={orderBy === col.id ? order : "asc"}
                          onClick={() => handleSort(col.id)}
                        >
                          {col.label}
                        </TableSortLabel>
                      ) : (
                        col.label
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {paginatedFindings.map((finding, index) => (
                  <TableRow
                    key={`${finding.rule_id}-${finding.location}-${index}`}
                    hover
                    sx={{
                      "&:last-child td, &:last-child th": { border: 0 },
                      // A coloured edge on failing rows makes them findable
                      // while scrolling, without tinting the whole row.
                      ...(finding.status === "NON-COMPLIANT" && {
                        "& td:first-of-type": {
                          boxShadow: `inset 3px 0 0 ${
                            statusToken("NON-COMPLIANT").main
                          }`,
                        },
                      }),
                      ...(finding.status === "NOT_EVALUATED" && {
                        "& td": { color: "text.secondary" },
                      }),
                    }}
                  >
                    <TableCell
                      sx={clickable}
                      onClick={() => setDetail(finding)}
                    >
                      <Chip
                        label={finding.rule_id}
                        size="small"
                        sx={{
                          fontWeight: 600,
                          fontFamily: "monospace",
                          backgroundColor: "brand.violetTint",
                          color: "primary.main",
                          cursor: "pointer",
                        }}
                      />
                    </TableCell>
                    <TableCell
                      sx={clickable}
                      onClick={() => setDetail(finding)}
                    >
                      <Tooltip title="Why did this come out this way?" arrow>
                        <Box
                          component="span"
                          tabIndex={0}
                          role="button"
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              setDetail(finding);
                            }
                          }}
                          sx={{
                            textDecorationLine: "underline",
                            textDecorationStyle: "dotted",
                            textUnderlineOffset: "3px",
                            textDecorationColor: "rgba(68,0,153,0.4)",
                          }}
                        >
                          {finding.rule_name}
                        </Box>
                      </Tooltip>
                    </TableCell>
                    <TableCell
                      sx={clickable}
                      onClick={() => setDetail(finding)}
                    >
                      {getSeverityChip(finding.severity)}
                    </TableCell>
                    <TableCell
                      sx={clickable}
                      onClick={() => setDetail(finding)}
                    >
                      {getStatusChip(finding.status)}
                    </TableCell>
                    <TableCell>
                      {/* The feature name leads and the face number follows in
                          a tooltip: an engineer looks for "the Ø5.00 mm hole",
                          not for face 214, but the number is still the key that
                          matches the 3D view and any external tool. */}
                      <Tooltip title={finding.location}>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>
                          {finding.feature_label || finding.location}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      {finding.measurement_point ? (
                        <Typography
                          variant="body2"
                          sx={{ fontFamily: "monospace" }}
                        >
                          ({finding.measurement_point[0]},{" "}
                          {finding.measurement_point[1]},{" "}
                          {finding.measurement_point[2]})
                        </Typography>
                      ) : (
                        <Typography variant="caption" color="text.secondary">
                          N/A
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      {finding.measured || (
                        <Typography variant="body2" color="text.secondary">
                          N/A
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Tooltip
                        title={finding.reason || ""}
                        placement="top-start"
                        arrow
                        disableHoverListener={!finding.reason}
                      >
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{
                            maxWidth: 260,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {finding.reason || "-"}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <Tooltip
                        title={finding.agent_commentary || ""}
                        placement="top-start"
                        arrow
                        disableHoverListener={!finding.agent_commentary}
                      >
                        <Typography
                          variant="body2"
                          sx={{
                            maxWidth: 250,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {finding.agent_commentary || (
                            <Typography variant="body2" color="text.secondary">
                              -
                            </Typography>
                          )}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <TablePagination
            component="div"
            count={filteredFindings.length}
            page={page}
            onPageChange={handleChangePage}
            rowsPerPage={rowsPerPage}
            onRowsPerPageChange={handleChangeRowsPerPage}
            rowsPerPageOptions={[5, 10, 15, 25, 50]}
          />
        </>
      )}
      <FindingDetail
        open={detail != null}
        finding={detail}
        rule={detail ? rulesById.get(detail.rule_id) : null}
        onClose={() => setDetail(null)}
      />
    </Paper>
  );
}

export default FindingsTable;

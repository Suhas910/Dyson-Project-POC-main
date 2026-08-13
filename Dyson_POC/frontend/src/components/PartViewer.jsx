import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Paper,
  Typography,
  Chip,
  Button,
  CircularProgress,
  Alert,
} from "@mui/material";
import ViewInArIcon from "@mui/icons-material/ViewInAr";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { STATUS_TOKENS } from "../theme";

// Painted onto the model in this order of precedence: one failing rule on a
// face is what the eye should go to, however many rules that face also passes.
const STATUS_PRIORITY = [
  "NON-COMPLIANT",
  "ERROR",
  "NEEDS_REVIEW",
  "COMPLIANT",
  "NOT_EVALUATED",
];

// Faces no rule reached at all. Distinct from NOT_EVALUATED, which means a rule
// looked and declined to decide -- this means nothing looked.
const UNTOUCHED = "#C9CDD6";

const SELECTED = "#440099";

const MAGIC = "DFMMESH1";

/** Reads the binary mesh into typed arrays with no per-number parsing. */
function parseMesh(buffer) {
  const bytes = new Uint8Array(buffer);
  const magic = String.fromCharCode(...bytes.slice(0, 8));
  if (magic !== MAGIC) {
    throw new Error(`Not a DFM mesh (got "${magic}")`);
  }

  const headerLength = new DataView(buffer).getUint32(8, true);
  const header = JSON.parse(
    new TextDecoder().decode(bytes.subarray(12, 12 + headerLength))
  );

  const { vertexCount: v, triangleCount: t } = header;
  let offset = 12 + headerLength;

  const take = (Type, count) => {
    const array = new Type(buffer, offset, count);
    offset += count * Type.BYTES_PER_ELEMENT;
    return array;
  };

  return {
    header,
    positions: take(Float32Array, v * 3),
    normals: take(Float32Array, v * 3),
    faceIds: take(Uint32Array, v),
    indices: take(Uint32Array, t * 3),
  };
}

/** The worst status recorded against each face, from the findings table. */
function statusByFace(findings) {
  const rank = new Map(STATUS_PRIORITY.map((s, i) => [s, i]));
  const worst = new Map();

  findings.forEach((finding) => {
    // Findings name their location as "face 214"; part-level findings apply to
    // no face in particular and are left off the model rather than smeared
    // across all of it.
    const match = /^face (\d+)$/.exec(finding.location || "");
    if (!match) return;

    const faceId = Number(match[1]);
    const current = worst.get(faceId);
    const incoming = rank.get(finding.status);
    if (incoming === undefined) return;
    if (current === undefined || incoming < rank.get(current)) {
      worst.set(faceId, finding.status);
    }
  });

  return worst;
}

function PartViewer({ versionId, findings, onSelectFace, selectedFace }) {
  const mountRef = useRef(null);
  const sceneRef = useRef(null);
  const [mesh, setMesh] = useState(null);
  const [state, setState] = useState("loading");
  const [hoveredFace, setHoveredFace] = useState(null);

  const faceStatus = useMemo(() => statusByFace(findings || []), [findings]);
  // Names come with the mesh rather than from the findings: a face no rule
  // reached still has a name, and those are exactly the ones a user clicks
  // while asking what they are.
  const faceNames = useMemo(() => {
    const names = new Map();
    Object.entries(mesh?.header?.faceLabels || {}).forEach(([id, text]) => {
      names.set(Number(id), text);
    });
    return names;
  }, [mesh]);

  // --- Fetch -------------------------------------------------------------
  useEffect(() => {
    if (versionId == null) return undefined;

    let cancelled = false;
    setState("loading");
    setMesh(null);

    fetch(`/api/mesh/${versionId}`)
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.arrayBuffer();
      })
      .then((buffer) => {
        if (cancelled) return;
        setMesh(parseMesh(buffer));
        setState("ready");
      })
      .catch(() => {
        if (!cancelled) setState("unavailable");
      });

    return () => {
      cancelled = true;
    };
  }, [versionId]);

  // --- Scene -------------------------------------------------------------
  // Built once per mesh. Recolouring on selection is a separate effect that
  // writes into the existing colour attribute, so changing what is highlighted
  // never rebuilds the geometry or resets the camera the user has posed.
  useEffect(() => {
    const mount = mountRef.current;
    if (!mesh || !mount) return undefined;

    const { positions, normals, faceIds, indices, header } = mesh;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#F8F6FB");

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("normal", new THREE.BufferAttribute(normals, 3));
    geometry.setAttribute(
      "color",
      new THREE.BufferAttribute(new Float32Array(positions.length), 3)
    );
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));

    const material = new THREE.MeshStandardMaterial({
      vertexColors: true,
      metalness: 0.05,
      roughness: 0.62,
      flatShading: false,
    });
    const solid = new THREE.Mesh(geometry, material);
    scene.add(solid);

    // A wireframe overlay of the same geometry reads the part's edges without
    // a second tessellation. Without it a single-colour region loses all its
    // form under flat lighting.
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry, 25),
      new THREE.LineBasicMaterial({ color: "#041E42", opacity: 0.22, transparent: true })
    );
    scene.add(edges);

    const { min, max } = header.bbox;
    const centre = new THREE.Vector3(
      (min[0] + max[0]) / 2,
      (min[1] + max[1]) / 2,
      (min[2] + max[2]) / 2
    );
    const radius =
      0.5 *
      Math.max(
        1e-3,
        Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2])
      );

    const camera = new THREE.PerspectiveCamera(
      42,
      mount.clientWidth / mount.clientHeight,
      radius / 100,
      radius * 100
    );
    // A three-quarter view off the pull direction: it shows top, front and side
    // at once, so draft and wall features are all visible before the user has
    // touched the model.
    // Framed on the bounding sphere, then pulled in: a box's bounding sphere is
    // much larger than the box, so fitting the sphere exactly leaves the part
    // sitting small in the middle of a lot of empty background.
    const distance = (radius / Math.sin((42 * Math.PI) / 360)) * 0.88;
    camera.position.set(
      centre.x + distance * 0.62,
      centre.y - distance * 0.62,
      centre.z + distance * 0.48
    );
    camera.up.set(0, 0, 1);
    camera.lookAt(centre);

    // Three.js has used physical light units since r155: the diffuse term is
    // divided by PI, so intensities that look right are roughly PI x what the
    // old renderer wanted. Under-lighting here would darken the status colours
    // until a red face and a grey one stopped being distinguishable, which is
    // the one thing this view has to get right.
    scene.add(new THREE.AmbientLight(0xffffff, 2.0));
    const key = new THREE.DirectionalLight(0xffffff, 2.4);
    key.position.set(1, -1, 1.4);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 1.0);
    fill.position.set(-1, 1, -0.6);
    scene.add(fill);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.copy(centre);
    controls.enableDamping = true;
    controls.update();

    let frame;
    const animate = () => {
      frame = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const resize = () => {
      if (!mount.clientWidth || !mount.clientHeight) return;
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    // --- Picking ---------------------------------------------------------
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();

    const faceUnder = (event) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObject(solid, false)[0];
      // Every vertex of a triangle belongs to the same face, so any one of them
      // answers the question.
      return hit ? faceIds[hit.face.a] : null;
    };

    // Distinguishing a click from the end of an orbit drag: without this,
    // releasing the mouse after rotating the part selects whatever is under
    // the cursor, which is never what was meant.
    let downAt = null;
    const onDown = (event) => {
      downAt = { x: event.clientX, y: event.clientY };
    };
    const onUp = (event) => {
      if (!downAt) return;
      const moved = Math.hypot(event.clientX - downAt.x, event.clientY - downAt.y);
      downAt = null;
      if (moved > 4) return;
      const faceId = faceUnder(event);
      onSelectFace?.(faceId);
    };
    const onMove = (event) => setHoveredFace(faceUnder(event));
    const onLeave = () => setHoveredFace(null);

    const canvas = renderer.domElement;
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerleave", onLeave);

    sceneRef.current = { geometry, camera, controls, centre, distance };

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
      controls.dispose();
      renderer.dispose();
      geometry.dispose();
      material.dispose();
      edges.geometry.dispose();
      edges.material.dispose();
      if (canvas.parentNode === mount) mount.removeChild(canvas);
      sceneRef.current = null;
    };
    // onSelectFace is intentionally excluded: it is a fresh closure on every
    // render of the parent, and depending on it would tear down and rebuild
    // the whole scene on every state change upstream.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mesh]);

  // --- Colouring ---------------------------------------------------------
  // Writes into the live colour attribute rather than rebuilding anything, so
  // selecting a face costs one buffer upload and the view does not flicker.
  useEffect(() => {
    const current = sceneRef.current;
    if (!mesh || !current) return;

    const colours = current.geometry.getAttribute("color");
    const { faceIds } = mesh;
    const cache = new Map();

    const colourFor = (faceId) => {
      if (faceId === selectedFace) return SELECTED;
      const status = faceStatus.get(faceId);
      return status ? STATUS_TOKENS[status].main : UNTOUCHED;
    };

    const scratch = new THREE.Color();
    for (let i = 0; i < faceIds.length; i += 1) {
      const faceId = faceIds[i];
      let rgb = cache.get(faceId);
      if (!rgb) {
        // setStyle applies the sRGB→linear conversion the renderer expects, so
        // the painted greens and reds match the chips in the findings table
        // rather than coming out washed out.
        scratch.setStyle(colourFor(faceId), THREE.SRGBColorSpace);
        rgb = [scratch.r, scratch.g, scratch.b];
        cache.set(faceId, rgb);
      }
      colours.setXYZ(i, rgb[0], rgb[1], rgb[2]);
    }
    colours.needsUpdate = true;
  }, [mesh, faceStatus, selectedFace]);

  const resetView = () => {
    const current = sceneRef.current;
    if (!current) return;
    const { camera, controls, centre, distance } = current;
    camera.position.set(
      centre.x + distance * 0.62,
      centre.y - distance * 0.62,
      centre.z + distance * 0.48
    );
    controls.target.copy(centre);
    controls.update();
  };

  const legend = useMemo(() => {
    const counts = new Map();
    faceStatus.forEach((status) => {
      counts.set(status, (counts.get(status) || 0) + 1);
    });
    return STATUS_PRIORITY.filter((s) => counts.has(s)).map((s) => ({
      status: s,
      count: counts.get(s),
      token: STATUS_TOKENS[s],
    }));
  }, [faceStatus]);

  if (versionId == null) return null;

  return (
    <Paper sx={{ mb: 2, overflow: "hidden" }}>
      <Box
        sx={{
          px: 2,
          py: 1.5,
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          flexWrap: "wrap",
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <ViewInArIcon sx={{ color: "primary.main" }} />
        <Typography variant="h6" sx={{ flexGrow: 1 }}>
          Part Model
        </Typography>

        {state === "ready" && (
          <>
            <Typography variant="caption" color="text.secondary">
              {hoveredFace != null
                ? `${faceNames.get(hoveredFace) || `Face ${hoveredFace}`}${
                    faceStatus.get(hoveredFace)
                      ? ` · ${STATUS_TOKENS[faceStatus.get(hoveredFace)].label}`
                      : " · no rule reached this face"
                  }`
                : "Drag to rotate · scroll to zoom · click a face"}
            </Typography>
            <Button size="small" startIcon={<RestartAltIcon />} onClick={resetView}>
              Reset view
            </Button>
          </>
        )}
      </Box>

      {state === "loading" && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress size={28} />
        </Box>
      )}

      {state === "unavailable" && (
        <Alert severity="info" sx={{ m: 2 }}>
          No 3D view was stored for this analysis. The findings below are
          unaffected — only the visualisation is missing.
        </Alert>
      )}

      <Box
        ref={mountRef}
        sx={{
          height: state === "ready" ? 420 : 0,
          width: "100%",
          cursor: hoveredFace != null ? "pointer" : "grab",
        }}
      />

      {state === "ready" && (
        <Box
          sx={{
            px: 2,
            py: 1.25,
            display: "flex",
            gap: 1,
            flexWrap: "wrap",
            alignItems: "center",
            borderTop: 1,
            borderColor: "divider",
          }}
        >
          {legend.map(({ status, count, token }) => (
            <Chip
              key={status}
              size="small"
              label={`${token.label} · ${count} ${count === 1 ? "face" : "faces"}`}
              sx={{
                backgroundColor: token.tint,
                color: token.text || token.main,
                border: `1px solid ${token.border}`,
                fontWeight: 600,
              }}
            />
          ))}
          <Chip
            size="small"
            label={`${mesh.header.faceCount - faceStatus.size} faces no rule reached`}
            sx={{
              backgroundColor: "#F1F2F5",
              color: "#565E6B",
              border: `1px solid ${UNTOUCHED}`,
              fontWeight: 600,
            }}
          />
          {selectedFace != null && (
            <Chip
              size="small"
              label={`Showing ${faceNames.get(selectedFace) || `face ${selectedFace}`}`}
              onDelete={() => onSelectFace?.(null)}
              sx={{
                ml: "auto",
                backgroundColor: "#F3EEFA",
                color: SELECTED,
                border: `1px solid ${SELECTED}`,
                fontWeight: 700,
              }}
            />
          )}
        </Box>
      )}
    </Paper>
  );
}

export default PartViewer;

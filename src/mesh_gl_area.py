import os
import math
import ctypes
import numpy as np
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from OpenGL import GL

class GLView(Gtk.GLArea):
    def __init__(self, meshes, colors):
        super().__init__()
        # Request OpenGL 3.3 core context with depth buffer
        self.set_required_version(3, 3)
        self.set_has_depth_buffer(True)

        # Store meshes and normalize colors to [0,1] with 50% alpha
        self.meshes = meshes
        self.colors = [(r/255.0, g/255.0, b/255.0, 0.5) for r, g, b in colors]
        self.initialized = False

        # Connect signals
        self.connect('realize', self.on_realize)
        self.connect('render', self.on_render)
        self.connect('unrealize', self.on_unrealize)

    def on_realize(self, area):
        # Debug: mesh info
        print(f"DEBUG: on_realize called: {len(self.meshes)} meshes")
        for idx, mesh in enumerate(self.meshes):
            print(f"DEBUG: mesh {idx}: vertices={len(mesh.vertices)}, faces={len(mesh.faces)}")

        # Compute scene center and camera distance (no GL calls here)
        mins = np.full(3, np.inf)
        maxs = np.full(3, -np.inf)
        for mesh in self.meshes:
            v = np.array(mesh.vertices, dtype=np.float32)
            mins = np.minimum(mins, v.min(axis=0))
            maxs = np.maximum(maxs, v.max(axis=0))
        center = (mins + maxs) * 0.5
        radius = np.linalg.norm(maxs - mins) * 0.5
        self.scene_center = center
        self.camera_distance = max(3 * radius, 5.0)
        print(f"DEBUG: scene_center={self.scene_center}, camera_distance={self.camera_distance}")

    def on_render(self, area, context):
        # Ensure the provided context is current
        context.make_current()
        print(f"DEBUG: on_render called: initialized={self.initialized}")

        if not self.initialized:
            self._init_gl_resources()
            self.initialized = True
            print(f"DEBUG: init complete: {len(self.vaos)} VAOs, index_counts={self.index_counts}")

        self._draw_frame(area)
        return True

    def _init_gl_resources(self):
        print("DEBUG: _init_gl_resources starting")
        # Enable depth testing and blending
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

        # Compile shaders
        vert_shader = GL.glCreateShader(GL.GL_VERTEX_SHADER)
        frag_shader = GL.glCreateShader(GL.GL_FRAGMENT_SHADER)
        vert_src = b"""
#version 330 core
layout(location=0) in vec3 position;
uniform mat4 MVP;
void main() { gl_Position = MVP * vec4(position, 1.0); }
"""
        frag_src = b"""
#version 330 core
uniform vec4 color;
out vec4 fragColor;
void main() { fragColor = color; }
"""
        GL.glShaderSource(vert_shader, vert_src)
        GL.glCompileShader(vert_shader)
        if GL.glGetShaderiv(vert_shader, GL.GL_COMPILE_STATUS) != GL.GL_TRUE:
            print(GL.glGetShaderInfoLog(vert_shader))
        GL.glShaderSource(frag_shader, frag_src)
        GL.glCompileShader(frag_shader)
        if GL.glGetShaderiv(frag_shader, GL.GL_COMPILE_STATUS) != GL.GL_TRUE:
            print(GL.glGetShaderInfoLog(frag_shader))

        self.program = GL.glCreateProgram()
        GL.glAttachShader(self.program, vert_shader)
        GL.glAttachShader(self.program, frag_shader)
        GL.glLinkProgram(self.program)
        if GL.glGetProgramiv(self.program, GL.GL_LINK_STATUS) != GL.GL_TRUE:
            print(GL.glGetProgramInfoLog(self.program))
        GL.glDeleteShader(vert_shader)
        GL.glDeleteShader(frag_shader)

                # Uniform locations
        # Use string names so glGetUniformLocation returns valid locations
        self.mvp_loc = GL.glGetUniformLocation(self.program, "MVP")
        self.color_loc = GL.glGetUniformLocation(self.program, "color")

        # Upload meshes to VAOs to VAOs
        num_meshes = len(self.meshes)
        print(f"DEBUG: uploading {num_meshes} meshes to GPU")
        self.vaos = []
        self.index_counts = []
        for idx, mesh in enumerate(self.meshes):
            print(f"DEBUG: uploading mesh {idx}")
            verts = np.array(mesh.vertices, dtype=np.float32) - self.scene_center  # center meshes at origin
            inds = np.array(mesh.faces, dtype=np.uint32).ravel()

            vao = GL.glGenVertexArrays(1)
            vbo = GL.glGenBuffers(1)
            ebo = GL.glGenBuffers(1)
            GL.glBindVertexArray(vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ebo)
            GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, inds.nbytes, inds, GL.GL_STATIC_DRAW)
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, ctypes.c_void_p(0))
            GL.glBindVertexArray(0)

            self.vaos.append(vao)
            self.index_counts.append(inds.size)
        print(f"DEBUG: finished uploading VAOs={self.vaos}, counts={self.index_counts}")

        # Release CPU mesh data
        self.meshes = None

    def _draw_frame(self, area):
        print("DEBUG: _draw_frame starting")
        w, h = area.get_allocated_width(), area.get_allocated_height()
        print(f"DEBUG: viewport size {w}x{h}")
        GL.glViewport(0, 0, w, h)
        GL.glClearColor(0, 0, 0, 1)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

                        # Compute projection matrix (45° FOV, aspect, near/far tuned for small scene)
        aspect = w / h if h else 1.0
        fov = math.radians(45.0)
        f = 1.0 / math.tan(fov / 2.0)
        near = 0.01  # 1cm near plane
        far = max(self.camera_distance * 3.0, 1.0)  # ensure far at least 1.0
        proj = np.array([
            [f / aspect, 0, 0, 0],
            [0, f,       0, 0],
            [0, 0, (far + near) / (near - far), -1],
            [0, 0, (2 * far * near) / (near - far),  0]
        ], dtype=np.float32)

        # Compute view matrix via lookAt: camera offset on Y and Z to avoid gimbal lock
        eye = self.scene_center + np.array([0.0, -self.camera_distance, self.camera_distance * 0.5], dtype=np.float32)
        target = self.scene_center
        up = np.array([0.0, 0.0, 1.0], dtype=np.float32)
        fwd = (target - eye); fwd /= np.linalg.norm(fwd)
        right = np.cross(fwd, up); right /= np.linalg.norm(right)
        cam_up = np.cross(right, fwd)
        R = np.eye(4, dtype=np.float32)
        R[:3, :3] = np.vstack([right, cam_up, -fwd])
        T = np.eye(4, dtype=np.float32)
        T[:3, 3] = -eye
        view = R @ T

        # Draw each mesh (disable blending for full visibility)
        GL.glUseProgram(self.program)
        mvp = proj @ view
        GL.glUniformMatrix4fv(self.mvp_loc, 1, GL.GL_FALSE, np.ascontiguousarray(mvp.T, dtype=np.float32))
        GL.glDisable(GL.GL_BLEND)
        print("DEBUG: drawing meshes with blending disabled")
        GL.glUseProgram(self.program)
        mvp = proj @ view
        GL.glUniformMatrix4fv(self.mvp_loc, 1, GL.GL_FALSE, np.ascontiguousarray(mvp.T, dtype=np.float32))
        GL.glDisable(GL.GL_BLEND)
        print("DEBUG: drawing meshes with blending disabled")
        for idx, (vao, count, color) in enumerate(zip(self.vaos, self.index_counts, self.colors)):
            print(f"DEBUG: draw call for mesh {idx}: VAO={vao}, count={count}, color={color}")
            GL.glBindVertexArray(vao)
            r, g, b, _ = color
            GL.glUniform4f(self.color_loc, r, g, b, 1.0)
            GL.glDrawElements(GL.GL_TRIANGLES, count, GL.GL_UNSIGNED_INT, None)
            err = GL.glGetError()
            print(f"DEBUG: glGetError after drawElements: {err}")
        GL.glBindVertexArray(0)
        GL.glEnable(GL.GL_BLEND)
        print("DEBUG: frame complete")
        print("DEBUG: frame complete")

    def on_unrealize(self, area):
        # Cleanup GL resources
        ctx = area.get_context()
        if ctx:
            ctx.make_current()
        if hasattr(self, 'vaos'):
            for vao in self.vaos:
                GL.glDeleteVertexArrays(1, [vao])
        if hasattr(self, 'index_counts'):
            # VBO and EBO cleanup omitted for brevity
            pass
        if hasattr(self, 'program'):
            GL.glDeleteProgram(self.program)

# Example launcher
if __name__ == '__main__':
    import trimesh
    cube = trimesh.creation.box()
    sphere = trimesh.creation.icosphere(subdivisions=2)
    colors = [(255, 100, 0), (0, 150, 255)]
    app = Gtk.Application()
    def activate(app):
        win = Gtk.ApplicationWindow(application=app)
        win.set_default_size(800, 600)
        view = GLView([cube, sphere], colors)
        win.set_child(view)
        win.present()
    app.connect('activate', activate)
    app.run()
